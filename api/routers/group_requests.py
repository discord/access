"""Group requests router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter
from sqlalchemy.orm import joinedload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, GroupRequest, OktaUser
from api.operations import ApproveGroupRequest, CreateGroupRequest, RejectGroupRequest
from api.pagination import paginate
from api.schemas import CreateGroupRequestBody, GroupRequestDetail, ResolveGroupRequestBody
from api.schemas._serialize import safe_dump
from api.schemas.requests_schemas import _AppGroupRequestBody

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
    body: CreateGroupRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    requester = db.get(OktaUser, current_user_id)
    if requester is None:
        raise HTTPException(404, "Requester not found")
    requested_app_id = body.requested_app_id if isinstance(body, _AppGroupRequestBody) else None
    gr = CreateGroupRequest(
        requester_user=requester,
        requested_group_name=body.requested_group_name,
        requested_group_description=body.requested_group_description or "",
        requested_group_type=body.requested_group_type,
        requested_app_id=requested_app_id,
        requested_group_tags=body.requested_group_tags,
        requested_ownership_ending_at=body.requested_ownership_ending_at,
        request_reason=body.request_reason or "",
    ).execute()
    refreshed = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == gr.id).first()
    return safe_dump(_adapter, refreshed)


@router.put("/{group_request_id}", name="group_request_by_id_put")
def put_group_request(
    group_request_id: str,
    body: ResolveGroupRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    from api.auth.permissions import is_access_admin
    from api.models.app_group import get_app_managers

    gr = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")

    # Authorization: requester can always reject their own; otherwise admin
    # or app-owner-of-the-target-app.
    if gr.requester_user_id == current_user_id:
        if body.approved:
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
    if body.resolved_group_name is not None:
        gr.resolved_group_name = body.resolved_group_name
    if body.resolved_group_description is not None:
        gr.resolved_group_description = body.resolved_group_description
    if body.resolved_group_type is not None:
        gr.resolved_group_type = body.resolved_group_type
    if body.resolved_app_id is not None:
        gr.resolved_app_id = body.resolved_app_id
    if body.resolved_group_tags is not None:
        gr.resolved_group_tags = body.resolved_group_tags
    if body.resolved_ownership_ending_at is not None:
        gr.resolved_ownership_ending_at = body.resolved_ownership_ending_at

    db.commit()

    resolution_reason = body.resolution_reason or body.reason or ""
    if body.approved:
        ApproveGroupRequest(
            group_request=gr,
            approver_user=current_user_id,
            approval_reason=resolution_reason,
        ).execute()
    else:
        RejectGroupRequest(
            group_request=gr,
            current_user_id=current_user_id,
            rejection_reason=resolution_reason,
            notify_requester=gr.requester_user_id != current_user_id,
        ).execute()
    refreshed = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    return safe_dump(_adapter, refreshed)
