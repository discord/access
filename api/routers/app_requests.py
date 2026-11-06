"""App requests router.

Any user or service principal may request an app; only Access admins approve
one. Reads are open to every authenticated user.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy import String, cast, false, func, or_, select
from sqlalchemy.orm import aliased, joinedload

from api.auth.dependencies import CurrentRequester, CurrentUserId
from api.auth.permissions import is_access_admin
from api.database import DbSession
from api.models import AccessRequestStatus, AppRequest, OktaUser, ServicePrincipal
from api.operations import ApproveAppRequest, CreateAppRequest, RejectAppRequest
from api.pagination import Page, validated
from api.routers._eager import service_principal_options
from api.routers._fan_out import defer_fan_out
from api.routers._named_principals import attach_named_principals
from api.schemas.app_requests import (
    AppRequestDetail,
    CreateAppRequestBody,
    ResolveAppRequestBody,
    SearchAppRequestQuery,
)

router = APIRouter(prefix="/api/app-requests", tags=["app-requests"], dependencies=[Depends(defer_fan_out)])


def _load_options() -> tuple:
    return (
        joinedload(AppRequest.requester),
        joinedload(AppRequest.requester_service_principal).options(*service_principal_options()),
        joinedload(AppRequest.resolver),
    )


async def _attach_named_principals(db: DbSession, details: Sequence[AppRequestDetail]) -> None:
    await attach_named_principals(
        db,
        details,
        lambda d: [
            *d.requested_owner_user_ids,
            *(u for g in d.requested_app_groups for u in (*g.owner_user_ids, *g.member_user_ids)),
        ],
        lambda d: [
            *d.requested_owner_role_ids,
            *(r for g in d.requested_app_groups for r in (*g.owner_role_ids, *g.member_role_ids)),
        ],
    )


async def _load(db: DbSession, app_request_id: str) -> AppRequest | None:
    return (
        await db.scalars(select(AppRequest).options(*_load_options()).where(AppRequest.id == app_request_id))
    ).first()


async def _detail(db: DbSession, app_request_id: str) -> AppRequestDetail:
    db.expire_all()
    app_request = await _load(db, app_request_id)
    assert app_request is not None
    detail = AppRequestDetail.model_validate(app_request, from_attributes=True)
    await _attach_named_principals(db, [detail])
    return detail


@router.get("", name="app_requests")
async def list_app_requests(
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchAppRequestQuery, Query()],
) -> Page[AppRequestDetail]:
    stmt = select(AppRequest).options(*_load_options()).order_by(AppRequest.created_at.desc())

    if q_args.status:
        stmt = stmt.where(AppRequest.status == q_args.status)

    if q_args.requester_user_id:
        if q_args.requester_user_id == "@me":
            stmt = stmt.where(
                or_(
                    AppRequest.requester_user_id == current_user_id,
                    AppRequest.requester_service_principal_id == current_user_id,
                )
            )
        else:
            requester_alias = aliased(OktaUser)
            requester_sp_alias = aliased(ServicePrincipal)
            stmt = (
                stmt.outerjoin(AppRequest.requester.of_type(requester_alias))
                .outerjoin(AppRequest.requester_service_principal.of_type(requester_sp_alias))
                .where(
                    or_(
                        AppRequest.requester_user_id == q_args.requester_user_id,
                        requester_alias.email.ilike(q_args.requester_user_id),
                        AppRequest.requester_service_principal_id == q_args.requester_user_id,
                        requester_sp_alias.display_name.ilike(q_args.requester_user_id),
                    )
                )
            )

    if q_args.assignee_user_id:
        # "Requests I can resolve": Access admins resolve every app request, except their own.
        assignee_user_id = current_user_id if q_args.assignee_user_id == "@me" else q_args.assignee_user_id
        assignee = (
            await db.scalars(
                select(OktaUser).where(or_(OktaUser.id == assignee_user_id, OktaUser.email.ilike(assignee_user_id)))
            )
        ).first()
        if assignee is not None and await is_access_admin(db, assignee.id):
            stmt = stmt.where(or_(AppRequest.requester_user_id.is_(None), AppRequest.requester_user_id != assignee.id))
        else:
            stmt = stmt.where(false())

    if q_args.resolver_user_id:
        resolver_user_id = current_user_id if q_args.resolver_user_id == "@me" else q_args.resolver_user_id
        resolver_alias = aliased(OktaUser)
        stmt = stmt.outerjoin(AppRequest.resolver.of_type(resolver_alias)).where(
            or_(AppRequest.resolver_user_id == resolver_user_id, resolver_alias.email.ilike(resolver_user_id))
        )

    if q_args.q:
        like = f"%{q_args.q}%"
        q_requester = aliased(OktaUser)
        q_requester_sp = aliased(ServicePrincipal)
        stmt = (
            stmt.outerjoin(AppRequest.requester.of_type(q_requester))
            .outerjoin(AppRequest.requester_service_principal.of_type(q_requester_sp))
            .where(
                or_(
                    AppRequest.id.like(f"{q_args.q}%"),
                    cast(AppRequest.status, String).ilike(like),
                    AppRequest.requested_app_name.ilike(like),
                    AppRequest.requested_app_description.ilike(like),
                    q_requester.email.ilike(like),
                    q_requester.display_name.ilike(like),
                    q_requester_sp.display_name.ilike(like),
                )
            )
        )

    page = await apaginate(db, stmt, transformer=validated(AppRequestDetail))
    await _attach_named_principals(db, page.items)
    return page


@router.get("/{app_request_id}", name="app_request_by_id")
async def get_app_request(app_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> AppRequestDetail:
    if await _load(db, app_request_id) is None:
        raise HTTPException(404, "Not Found")
    return await _detail(db, app_request_id)


@router.post("", name="app_requests_create", status_code=201)
async def post_app_request(
    body: CreateAppRequestBody,
    db: DbSession,
    requester: CurrentRequester,
) -> AppRequestDetail:
    # A retry replaces the same requester's pending request for the same app name.
    requester_column = (
        AppRequest.requester_service_principal_id
        if isinstance(requester, ServicePrincipal)
        else AppRequest.requester_user_id
    )
    priors = (
        await db.scalars(
            select(AppRequest)
            .where(func.lower(AppRequest.requested_app_name) == func.lower(body.requested_app_name))
            .where(requester_column == requester.id)
            .where(AppRequest.status == AccessRequestStatus.PENDING)
            .where(AppRequest.resolved_at.is_(None))
        )
    ).all()
    for prior in priors:
        await RejectAppRequest(
            app_request=prior,
            rejection_reason="Closed due to duplicate app request creation",
            notify_requester=False,
            current_user_id=requester.id,
        ).execute()

    app_request = await CreateAppRequest(
        requester=requester,
        requested_app_name=body.requested_app_name,
        requested_app_description=body.requested_app_description,
        requested_app_tags=body.requested_app_tags,
        requested_owner_user_ids=body.requested_owner_user_ids,
        requested_owner_role_ids=body.requested_owner_role_ids,
        requested_app_groups=[g.model_dump() for g in body.requested_app_groups],
        requested_ownership_ending_at=body.requested_ownership_ending_at,
        requested_membership_ending_at=body.requested_membership_ending_at,
        request_reason=body.request_reason or "",
    ).execute()
    return await _detail(db, app_request.id)


@router.put("/{app_request_id}", name="app_request_by_id_put")
async def put_app_request(
    app_request_id: str,
    body: ResolveAppRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> AppRequestDetail:
    app_request = await _load(db, app_request_id)
    if app_request is None:
        raise HTTPException(404, "Not Found")

    # The requester may withdraw its own request; only Access admins resolve others.
    is_requester = current_user_id in (app_request.requester_user_id, app_request.requester_service_principal_id)
    if is_requester:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not await is_access_admin(db, current_user_id):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    if app_request.status != AccessRequestStatus.PENDING or app_request.resolved_at is not None:
        raise HTTPException(409, "App request is not pending")

    if body.approved:
        if body.resolved_ownership_ending_at is not None:
            app_request.resolved_ownership_ending_at = body.resolved_ownership_ending_at
        if body.resolved_membership_ending_at is not None:
            app_request.resolved_membership_ending_at = body.resolved_membership_ending_at
        await db.commit()
        await ApproveAppRequest(
            app_request=app_request_id, approver_user=current_user_id, approval_reason=body.reason or ""
        ).execute()
    else:
        await RejectAppRequest(
            app_request=app_request_id,
            rejection_reason=body.reason or "",
            notify_requester=not is_requester,
            current_user_id=current_user_id,
        ).execute()
    return await _detail(db, app_request_id)
