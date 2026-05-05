"""Role requests router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.operations import ApproveRoleRequest, CreateRoleRequest, RejectRoleRequest
from api.pagination import paginate
from api.schemas import CreateRoleRequestBody, ResolveRoleRequestBody, RoleRequestDetail
from api.schemas._serialize import safe_dump

router = APIRouter(prefix="/api/role-requests", tags=["role-requests"])
_adapter = TypeAdapter(RoleRequestDetail)


@router.get("", name="role_requests")
def list_role_requests(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = db.query(RoleRequest).order_by(RoleRequest.created_at.desc())
    return paginate(request, query, _adapter)


@router.get("/{role_request_id}", name="role_request_by_id")
def get_role_request(role_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    rr = db.query(RoleRequest).filter(RoleRequest.id == role_request_id).first()
    if rr is None:
        raise HTTPException(404, "Not Found")
    return safe_dump(_adapter, rr)


@router.post("", name="role_requests_create", status_code=201)
def post_role_request(
    body: CreateRoleRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    from api.auth import permissions as _perms

    requester = db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first()
    role = db.query(RoleGroup).filter(RoleGroup.deleted_at.is_(None)).filter(RoleGroup.id == body.role_id).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    if requester is None or not _perms.can_manage_group(db, current_user_id, role):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    group = db.query(OktaGroup).filter(OktaGroup.deleted_at.is_(None)).filter(OktaGroup.id == body.group_id).first()
    if group is None:
        raise HTTPException(404, "Not Found")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")
    if type(group) is RoleGroup:
        raise HTTPException(400, "Role requests may only be made for groups and app groups (not roles).")

    # Close any existing pending duplicate requests
    existing = (
        db.query(RoleRequest)
        .filter(RoleRequest.requester_user_id == current_user_id)
        .filter(RoleRequest.requester_role_id == body.role_id)
        .filter(RoleRequest.requested_group_id == body.group_id)
        .filter(RoleRequest.request_ownership == body.group_owner)
        .filter(RoleRequest.status == AccessRequestStatus.PENDING)
        .filter(RoleRequest.resolved_at.is_(None))
        .all()
    )
    for old in existing:
        RejectRoleRequest(
            role_request=old,
            rejection_reason="Closed due to duplicate role request creation",
            notify_requester=False,
            current_user_id=current_user_id,
        ).execute()
    rr = CreateRoleRequest(
        requester_user=requester,
        requester_role=role,
        requested_group=group,
        request_ownership=body.group_owner,
        request_reason=body.reason or "",
        request_ending_at=body.ending_at,
    ).execute()
    return safe_dump(_adapter, rr)


@router.put("/{role_request_id}", name="role_request_by_id_put")
def put_role_request(
    role_request_id: str,
    body: ResolveRoleRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    from sqlalchemy.orm import joinedload

    from api.auth import permissions as _perms

    rr = (
        db.query(RoleRequest)
        .options(
            joinedload(RoleRequest.active_requested_group),
            joinedload(RoleRequest.active_requester_role),
        )
        .filter(RoleRequest.id == role_request_id)
        .first()
    )
    if rr is None:
        raise HTTPException(404, "Not Found")

    # Requester can always reject their own request, but cannot approve it.
    if rr.requester_user_id == current_user_id:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not _perms.can_manage_group(db, current_user_id, rr.active_requested_group):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    if rr.status != AccessRequestStatus.PENDING:
        raise HTTPException(400, "Role request has already been resolved")
    if body.approved:
        ApproveRoleRequest(
            role_request=rr,
            approver_user=current_user_id,
            approval_reason=body.reason or "",
            ending_at=body.ending_at,
        ).execute()
    else:
        RejectRoleRequest(
            role_request=rr,
            current_user_id=current_user_id,
            rejection_reason=body.reason or "",
            notify_requester=rr.requester_user_id != current_user_id,
        ).execute()
    refreshed = db.query(RoleRequest).filter(RoleRequest.id == role_request_id).first()
    return safe_dump(_adapter, refreshed)
