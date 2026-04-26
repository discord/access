"""Role requests router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import TypeAdapter
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.operations import ApproveRoleRequest, CreateRoleRequest, RejectRoleRequest
from api.pagination import paginate
from api.schemas import RoleRequestOut

router = APIRouter(prefix="/api/role-requests", tags=["role-requests"])
_adapter = TypeAdapter(RoleRequestOut)


@router.get("", name="role_requests")
def list_role_requests(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = db.query(RoleRequest).order_by(RoleRequest.created_at.desc())
    return paginate(request, query, _adapter)


@router.get("/{role_request_id}", name="role_request_by_id")
def get_role_request(role_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    rr = db.query(RoleRequest).filter(RoleRequest.id == role_request_id).first()
    if rr is None:
        raise HTTPException(404, "Not Found")
    return _adapter.dump_python(_adapter.validate_python(rr, from_attributes=True), mode="json")


@router.post("", name="role_requests_create", status_code=201)
def post_role_request(
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    requester = db.get(OktaUser, current_user_id)
    if requester is None:
        raise HTTPException(404, "Requester not found")
    role = db.query(RoleGroup).filter(RoleGroup.id == body.get("role_id")).first()
    group = db.query(OktaGroup).filter(OktaGroup.id == body.get("group_id")).first()
    if role is None or group is None:
        raise HTTPException(404, "Role or group not found")
    rr = CreateRoleRequest(
        requester_user=requester,
        requester_role=role,
        requested_group=group,
        request_ownership=bool(body.get("group_owner", False)),
        request_reason=body.get("reason", "") or "",
        request_ending_at=body.get("ending_at"),
    ).execute()
    return _adapter.dump_python(_adapter.validate_python(rr, from_attributes=True), mode="json")


@router.put("/{role_request_id}", name="role_request_by_id_put")
def put_role_request(
    role_request_id: str,
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    rr = db.query(RoleRequest).filter(RoleRequest.id == role_request_id).first()
    if rr is None:
        raise HTTPException(404, "Not Found")
    if rr.status != AccessRequestStatus.PENDING:
        raise HTTPException(400, "Role request has already been resolved")
    approver = db.get(OktaUser, current_user_id)
    approver_email = approver.email if approver is not None else None
    if bool(body.get("approved")):
        ApproveRoleRequest(
            role_request=rr,
            approver_id=current_user_id,
            approver_email=approver_email,
            approval_reason=body.get("reason", "") or "",
            ending_at=body.get("ending_at"),
        ).execute()
    else:
        RejectRoleRequest(
            role_request=rr,
            rejector_id=current_user_id,
            rejector_email=approver_email,
            rejection_reason=body.get("reason", "") or "",
        ).execute()
    refreshed = db.query(RoleRequest).filter(RoleRequest.id == role_request_id).first()
    return _adapter.dump_python(_adapter.validate_python(refreshed, from_attributes=True), mode="json")
