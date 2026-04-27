"""Access requests router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import String, cast
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations import ApproveAccessRequest, CreateAccessRequest, RejectAccessRequest
from api.pagination import paginate
from api.schemas import AccessRequestDetail
from api.schemas._serialize import safe_dump
from api.schemas.rfc822 import parse_datetime_value

router = APIRouter(prefix="/api/requests", tags=["access-requests"])

_adapter = TypeAdapter(AccessRequestDetail)


def _load_options() -> tuple:
    return (
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AccessRequest.requester),
        joinedload(AccessRequest.active_requester),
        selectinload(AccessRequest.requested_group),
        selectinload(AccessRequest.active_requested_group),
        joinedload(AccessRequest.resolver),
        joinedload(AccessRequest.active_resolver),
    )


@router.get("", name="access_requests")
def list_access_requests(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(AccessRequest).options(*_load_options()).order_by(AccessRequest.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(
            _db.or_(
                cast(AccessRequest.status, String).ilike(like),
                AccessRequest.request_reason.ilike(like),
            )
        )
    return paginate(request, query, _adapter)


@router.get("/{access_request_id}", name="access_request_by_id")
def get_access_request(access_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    ar = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == access_request_id).first()
    if ar is None:
        raise HTTPException(404, "Not Found")
    return safe_dump(_adapter, ar)


@router.post("", name="access_requests_create", status_code=201)
def post_access_request(
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = body or {}
    group_id = body.get("group_id")
    if not group_id:
        raise HTTPException(400, "group_id is required")
    requester = db.get(OktaUser, current_user_id)
    if requester is None:
        raise HTTPException(404, "Requester not found")
    group = db.query(OktaGroup).filter(OktaGroup.id == group_id).filter(OktaGroup.deleted_at.is_(None)).first()
    if group is None:
        raise HTTPException(404, "Group not found")
    ar = CreateAccessRequest(
        requester_user=requester,
        requested_group=group,
        request_ownership=bool(body.get("group_owner", False)),
        request_reason=body.get("reason", "") or "",
        request_ending_at=parse_datetime_value(body.get("ending_at")),
    ).execute()
    refreshed = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == ar.id).first()
    return safe_dump(_adapter, refreshed)


@router.put("/{access_request_id}", name="access_request_by_id_put")
def put_access_request(
    access_request_id: str,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from sqlalchemy.orm import joinedload

    from api.auth.permissions import can_manage_group
    from api.operations.constraints import CheckForReason

    body = body or {}
    ar = (
        db.query(AccessRequest)
        .options(joinedload(AccessRequest.active_requested_group))
        .filter(AccessRequest.id == access_request_id)
        .first()
    )
    if ar is None:
        raise HTTPException(404, "Not Found")
    if "approved" not in body:
        raise HTTPException(400, "approved is required")

    approved = bool(body.get("approved"))

    # Requester can always reject their own request, but cannot approve it.
    if ar.requester_user_id == current_user_id:
        if approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not can_manage_group(db, current_user_id, ar.active_requested_group):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    if approved:
        valid, err_message = CheckForReason(
            group=ar.active_requested_group,
            reason=body.get("reason"),
            members_to_add=[ar.requester_user_id] if not ar.request_ownership else [],
            owners_to_add=[ar.requester_user_id] if ar.request_ownership else [],
        ).execute_for_group()
        if not valid:
            raise HTTPException(400, err_message)

    if ar.status != AccessRequestStatus.PENDING or ar.resolved_at is not None:
        raise HTTPException(400, "Access request is not pending")

    if approved:
        if not ar.requested_group.is_managed:
            raise HTTPException(400, "Groups not managed by Access cannot be modified")
        ApproveAccessRequest(
            access_request=ar,
            approver_user=current_user_id,
            approval_reason=body.get("reason", "") or "",
            ending_at=parse_datetime_value(body.get("ending_at")),
        ).execute()
    else:
        RejectAccessRequest(
            access_request=ar,
            rejection_reason=body.get("reason", "") or "",
            notify_requester=ar.requester_user_id != current_user_id,
            current_user_id=current_user_id,
        ).execute()
    refreshed = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == access_request_id).first()
    return safe_dump(_adapter, refreshed)
