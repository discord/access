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
from api.schemas._serialize import safe_dump

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
    return safe_dump(_adapter, gr)


@router.post("", name="group_requests_create", status_code=201)
def post_group_request(
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = body or {}
    if not body.get("group_type") or not body.get("group_name"):
        raise HTTPException(400, "group_type and group_name are required")
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
    return safe_dump(_adapter, gr)


@router.put("/{group_request_id}", name="group_request_by_id_put")
def put_group_request(
    group_request_id: str,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = body or {}
    gr = db.query(GroupRequest).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    if "approved" not in body:
        raise HTTPException(400, "approved is required")
    if gr.status != AccessRequestStatus.PENDING:
        raise HTTPException(400, "Group request has already been resolved")
    if bool(body.get("approved")):
        ApproveGroupRequest(
            group_request=gr,
            approver_user=current_user_id,
            approval_reason=body.get("reason", "") or "",
        ).execute()
    else:
        RejectGroupRequest(
            group_request=gr,
            current_user_id=current_user_id,
            rejection_reason=body.get("reason", "") or "",
        ).execute()
    refreshed = db.query(GroupRequest).filter(GroupRequest.id == group_request_id).first()
    return safe_dump(_adapter, refreshed)
