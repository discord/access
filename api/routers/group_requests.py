"""Group requests router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import TypeAdapter
from sqlalchemy.orm import joinedload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, GroupRequest, OktaUser
from api.operations import ApproveGroupRequest, CreateGroupRequest, RejectGroupRequest
from api.pagination import paginate
from api.schemas import GroupRequestDetail
from api.schemas._serialize import safe_dump

router = APIRouter(prefix="/api/group-requests", tags=["group-requests"])
_adapter = TypeAdapter(GroupRequestDetail)


def _load_options() -> tuple:
    return (
        joinedload(GroupRequest.requester),
        joinedload(GroupRequest.active_requester),
        joinedload(GroupRequest.resolver),
        joinedload(GroupRequest.active_resolver),
        joinedload(GroupRequest.approved_group),
    )


@router.get("", name="group_requests")
def list_group_requests(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = db.query(GroupRequest).options(*_load_options()).order_by(GroupRequest.created_at.desc())
    return paginate(request, query, _adapter)


@router.get("/{group_request_id}", name="group_request_by_id")
def get_group_request(group_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    gr = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    return safe_dump(_adapter, gr)


@router.post("", name="group_requests_create", status_code=201)
def post_group_request(
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from api.schemas.rfc822 import parse_datetime_value

    body = body or {}
    name = body.get("requested_group_name") or body.get("group_name")
    group_type = body.get("requested_group_type") or body.get("group_type")
    if not name or not group_type:
        raise HTTPException(400, "requested_group_name and requested_group_type are required")
    requester = db.get(OktaUser, current_user_id)
    if requester is None:
        raise HTTPException(404, "Requester not found")
    gr = CreateGroupRequest(
        requester_user=requester,
        requested_group_name=name,
        requested_group_description=body.get("requested_group_description", "") or "",
        requested_group_type=group_type,
        requested_app_id=body.get("requested_app_id") or body.get("app_id"),
        requested_group_tags=body.get("requested_group_tags") or [],
        requested_ownership_ending_at=parse_datetime_value(body.get("requested_ownership_ending_at")),
        request_reason=body.get("request_reason") or body.get("reason", "") or "",
    ).execute()
    refreshed = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == gr.id).first()
    return safe_dump(_adapter, refreshed)


@router.put("/{group_request_id}", name="group_request_by_id_put")
def put_group_request(
    group_request_id: str,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from api.auth.permissions import is_access_admin
    from api.models.app_group import get_app_managers
    from api.schemas.rfc822 import parse_datetime_value

    body = body or {}
    gr = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    if "approved" not in body:
        raise HTTPException(400, "approved is required")
    approved = bool(body.get("approved"))

    # Authorization: requester can always reject their own; otherwise admin
    # or app-owner-of-the-target-app.
    if gr.requester_user_id == current_user_id:
        if approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not is_access_admin(db, current_user_id):
        if gr.requested_app_id is not None:
            approver_ids = [u.id for u in get_app_managers(gr.requested_app_id)]
            if current_user_id not in approver_ids:
                raise HTTPException(403, "Current user is not allowed to perform this action")
        else:
            raise HTTPException(403, "Current user is not allowed to perform this action")

    if gr.status != AccessRequestStatus.PENDING or gr.resolved_at is not None:
        raise HTTPException(400, "Group request is not pending")

    # Update resolved_* fields if the body carried them.
    for field in (
        "resolved_group_name",
        "resolved_group_description",
        "resolved_group_type",
        "resolved_app_id",
        "resolved_group_tags",
    ):
        if field in body:
            setattr(gr, field, body[field])
    if "resolved_ownership_ending_at" in body:
        gr.resolved_ownership_ending_at = parse_datetime_value(body["resolved_ownership_ending_at"])

    db.commit()

    if approved:
        ApproveGroupRequest(
            group_request=gr,
            approver_user=current_user_id,
            approval_reason=body.get("resolution_reason", "") or body.get("reason", "") or "",
        ).execute()
    else:
        RejectGroupRequest(
            group_request=gr,
            current_user_id=current_user_id,
            rejection_reason=body.get("resolution_reason", "") or body.get("reason", "") or "",
            notify_requester=gr.requester_user_id != current_user_id,
        ).execute()
    refreshed = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    return safe_dump(_adapter, refreshed)
