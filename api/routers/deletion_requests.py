"""Deletion requests router.

A user or service principal may request that a group or an app be deleted;
anyone who could delete it directly may approve. API only. Reads are open to
every authenticated user.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, selectinload

from api.auth.dependencies import CurrentRequester, CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, DeletionRequest, ServicePrincipal
from api.operations import ApproveDeletionRequest, CreateDeletionRequest, RejectDeletionRequest
from api.operations._deletion_requests import can_approve, load_deletable_target
from api.pagination import Page, validated
from api.routers._eager import polymorphic_group_options, service_principal_options
from api.routers._fan_out import defer_fan_out
from api.schemas.deletion_requests import (
    CreateDeletionRequestBody,
    DeletionRequestDetail,
    ResolveDeletionRequestBody,
    SearchDeletionRequestQuery,
)

router = APIRouter(prefix="/api/deletion-requests", tags=["deletion-requests"], dependencies=[Depends(defer_fan_out)])


def _load_options() -> tuple:
    return (
        selectinload(DeletionRequest.group).options(*polymorphic_group_options()),
        joinedload(DeletionRequest.app),
        joinedload(DeletionRequest.requester),
        joinedload(DeletionRequest.requester_service_principal).options(*service_principal_options()),
        joinedload(DeletionRequest.resolver),
    )


async def _detail(db: DbSession, deletion_request_id: str) -> DeletionRequestDetail:
    db.expire_all()
    deletion_request = (
        await db.scalars(
            select(DeletionRequest).options(*_load_options()).where(DeletionRequest.id == deletion_request_id)
        )
    ).first()
    if deletion_request is None:
        raise HTTPException(404, "Not Found")
    return DeletionRequestDetail.model_validate(deletion_request, from_attributes=True)


@router.get("", name="deletion_requests")
async def list_deletion_requests(
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchDeletionRequestQuery, Query()],
) -> Page[DeletionRequestDetail]:
    stmt = select(DeletionRequest).options(*_load_options()).order_by(DeletionRequest.created_at.desc())
    if q_args.status:
        stmt = stmt.where(DeletionRequest.status == q_args.status)
    if q_args.requester_user_id:
        requester_id = current_user_id if q_args.requester_user_id == "@me" else q_args.requester_user_id
        stmt = stmt.where(
            or_(
                DeletionRequest.requester_user_id == requester_id,
                DeletionRequest.requester_service_principal_id == requester_id,
            )
        )
    if q_args.group_id:
        stmt = stmt.where(DeletionRequest.group_id == q_args.group_id)
    if q_args.app_id:
        stmt = stmt.where(DeletionRequest.app_id == q_args.app_id)
    if q_args.q:
        stmt = stmt.where(
            or_(DeletionRequest.id.like(f"{q_args.q}%"), DeletionRequest.request_reason.ilike(f"%{q_args.q}%"))
        )
    return await apaginate(db, stmt, transformer=validated(DeletionRequestDetail))


@router.get("/{deletion_request_id}", name="deletion_request_by_id")
async def get_deletion_request(
    deletion_request_id: str, db: DbSession, current_user_id: CurrentUserId
) -> DeletionRequestDetail:
    return await _detail(db, deletion_request_id)


@router.post("", name="deletion_requests_create", status_code=201)
async def post_deletion_request(
    body: CreateDeletionRequestBody,
    db: DbSession,
    requester: CurrentRequester,
) -> DeletionRequestDetail:
    # Validate the target before cancelling anything, so a bad retry leaves the pending one alone.
    await load_deletable_target(group_id=body.group_id, app_id=body.app_id)

    # A retry replaces the same requester's pending request for the same target.
    requester_column = (
        DeletionRequest.requester_service_principal_id
        if isinstance(requester, ServicePrincipal)
        else DeletionRequest.requester_user_id
    )
    target_column = DeletionRequest.group_id if body.group_id is not None else DeletionRequest.app_id
    priors = (
        await db.scalars(
            select(DeletionRequest)
            .where(requester_column == requester.id)
            .where(target_column == (body.group_id or body.app_id))
            .where(DeletionRequest.status == AccessRequestStatus.PENDING)
            .where(DeletionRequest.resolved_at.is_(None))
        )
    ).all()
    for prior in priors:
        await RejectDeletionRequest(
            deletion_request=prior,
            rejection_reason="Closed due to duplicate deletion request creation",
            notify_requester=False,
            current_user_id=requester.id,
        ).execute()

    deletion_request = await CreateDeletionRequest(
        requester=requester,
        group_id=body.group_id,
        app_id=body.app_id,
        request_reason=body.request_reason or "",
    ).execute()
    return await _detail(db, deletion_request.id)


@router.put("/{deletion_request_id}", name="deletion_request_by_id_put")
async def put_deletion_request(
    deletion_request_id: str,
    body: ResolveDeletionRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> DeletionRequestDetail:
    deletion_request = (
        await db.scalars(
            select(DeletionRequest).options(*_load_options()).where(DeletionRequest.id == deletion_request_id)
        )
    ).first()
    if deletion_request is None:
        raise HTTPException(404, "Not Found")

    # The requester may withdraw its own request; others need the right to delete the target.
    is_requester = current_user_id in (
        deletion_request.requester_user_id,
        deletion_request.requester_service_principal_id,
    )
    if is_requester:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif ServicePrincipal.is_service_principal_id(current_user_id) or not await can_approve(
        current_user_id, deletion_request.group, deletion_request.app
    ):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    if deletion_request.status != AccessRequestStatus.PENDING or deletion_request.resolved_at is not None:
        raise HTTPException(409, "Deletion request is not pending")

    if body.approved:
        await ApproveDeletionRequest(
            deletion_request=deletion_request_id, approver_user=current_user_id, approval_reason=body.reason or ""
        ).execute()
    else:
        await RejectDeletionRequest(
            deletion_request=deletion_request_id,
            rejection_reason=body.reason or "",
            notify_requester=not is_requester,
            current_user_id=current_user_id,
        ).execute()
    return await _detail(db, deletion_request_id)
