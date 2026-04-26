"""Group requests router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import TypeAdapter
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, GroupRequest, OktaUser
from api.operations import ApproveGroupRequest, CreateGroupRequest, RejectGroupRequest
from api.pagination import paginate
from api.schemas import GroupRequestOut

router = APIRouter(prefix="/api/group-requests", tags=["group-requests"])
_adapter = TypeAdapter(GroupRequestOut)


@router.get("", name="group_requests")
def list_group_requests(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = db.query(GroupRequest).order_by(GroupRequest.created_at.desc())
    return paginate(request, query, _adapter)


@router.get("/{group_request_id}", name="group_request_by_id")
def get_group_request(group_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    gr = db.query(GroupRequest).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    return _adapter.dump_python(_adapter.validate_python(gr, from_attributes=True), mode="json")


@router.post("", name="group_requests_create", status_code=201)
def post_group_request(
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    requester = db.get(OktaUser, current_user_id)
    if requester is None:
        raise HTTPException(404, "Requester not found")
    gr = CreateGroupRequest(
        requester=requester,
        group_type=body.get("group_type", ""),
        group_name=body.get("group_name", ""),
        app_id=body.get("app_id"),
        request_reason=body.get("reason", "") or "",
    ).execute()
    return _adapter.dump_python(_adapter.validate_python(gr, from_attributes=True), mode="json")


@router.put("/{group_request_id}", name="group_request_by_id_put")
def put_group_request(
    group_request_id: str,
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    gr = db.query(GroupRequest).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    if gr.status != AccessRequestStatus.PENDING:
        raise HTTPException(400, "Group request has already been resolved")
    approver = db.get(OktaUser, current_user_id)
    approver_email = approver.email if approver is not None else None
    if bool(body.get("approved")):
        ApproveGroupRequest(
            group_request=gr,
            approver_id=current_user_id,
            approver_email=approver_email,
            approval_reason=body.get("reason", "") or "",
        ).execute()
    else:
        RejectGroupRequest(
            group_request=gr,
            rejector_id=current_user_id,
            rejector_email=approver_email,
            rejection_reason=body.get("reason", "") or "",
        ).execute()
    refreshed = db.query(GroupRequest).filter(GroupRequest.id == group_request_id).first()
    return _adapter.dump_python(_adapter.validate_python(refreshed, from_attributes=True), mode="json")
