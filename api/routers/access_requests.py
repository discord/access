"""Access requests router."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations import ApproveAccessRequest, CreateAccessRequest, RejectAccessRequest
from api.pagination import paginate
from api.schemas import AccessRequestOut

router = APIRouter(prefix="/api/requests", tags=["access-requests"])

_adapter = TypeAdapter(AccessRequestOut)


def _load_options() -> tuple:
    return (
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AccessRequest.requester),
        joinedload(AccessRequest.active_requester),
        joinedload(AccessRequest.requested_group.of_type(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))),
        joinedload(AccessRequest.active_requested_group.of_type(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))),
        joinedload(AccessRequest.resolver),
        joinedload(AccessRequest.active_resolver),
    )


@router.get("", name="access_requests")
def list_access_requests(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(AccessRequest).options(*_load_options()).order_by(AccessRequest.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(_db.or_(
            AccessRequest.status.ilike(like),
            AccessRequest.request_reason.ilike(like),
        ))
    return paginate(request, query, _adapter)


@router.get("/{access_request_id}", name="access_request_by_id")
def get_access_request(access_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    ar = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == access_request_id).first()
    if ar is None:
        raise HTTPException(404, "Not Found")
    return _adapter.dump_python(_adapter.validate_python(ar, from_attributes=True), mode="json")


@router.post("", name="access_requests_create", status_code=201)
def post_access_request(
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
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
        request_ending_at=body.get("ending_at"),
    ).execute()
    refreshed = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == ar.id).first()
    return _adapter.dump_python(_adapter.validate_python(refreshed, from_attributes=True), mode="json")


@router.put("/{access_request_id}", name="access_request_by_id_put")
def put_access_request(
    access_request_id: str,
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    ar = db.query(AccessRequest).filter(AccessRequest.id == access_request_id).first()
    if ar is None:
        raise HTTPException(404, "Not Found")
    if ar.status != AccessRequestStatus.PENDING:
        raise HTTPException(400, "Access request has already been resolved")
    approver = db.get(OktaUser, current_user_id)
    approver_email = approver.email if approver is not None else None
    if bool(body.get("approved")):
        ApproveAccessRequest(
            access_request=ar,
            approver_id=current_user_id,
            approver_email=approver_email,
            approval_reason=body.get("reason", "") or "",
            ending_at=body.get("ending_at"),
        ).execute()
    else:
        RejectAccessRequest(
            access_request=ar,
            rejector_id=current_user_id,
            rejector_email=approver_email,
            rejection_reason=body.get("reason", "") or "",
        ).execute()
    refreshed = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == access_request_id).first()
    return _adapter.dump_python(_adapter.validate_python(refreshed, from_attributes=True), mode="json")
