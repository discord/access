"""Roles router. Roles are RoleGroups; this router exposes endpoints scoped
to that subtype. Most logic is delegated to the groups router via shared
helpers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import can_manage_group, is_access_admin
from api.database import DbSession
from api.extensions import db as _db
from api.models import OktaGroup, RoleGroup, RoleGroupMap
from api.operations import ModifyRoleGroups
from api.pagination import paginate
from api.schemas import GroupOut, GroupSummary
from api.schemas._serialize import safe_dump
from api.schemas.requests_schemas import RoleMember

router = APIRouter(prefix="/api/roles", tags=["roles"])
_role_adapter = TypeAdapter(GroupOut)
_role_summary_adapter = TypeAdapter(GroupSummary)


@router.get("", name="roles")
def list_roles(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = (
        db.query(RoleGroup)
        .filter(RoleGroup.deleted_at.is_(None))
        .order_by(func.lower(RoleGroup.name))
    )
    q = request.query_params.get("q", "")
    if q:
        like = f"%{q}%"
        query = query.filter(_db.or_(RoleGroup.name.ilike(like), RoleGroup.description.ilike(like)))
    return paginate(request, query, _role_summary_adapter)


@router.get("/{role_id}", name="role_by_id")
def get_role(role_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    role = (
        db.query(RoleGroup)
        .filter(_db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        .first()
    )
    if role is None:
        raise HTTPException(404, "Not Found")
    return safe_dump(_role_adapter, role)


@router.get("/{role_id}/audit", name="role_audit_by_id")
def get_role_audit(role_id: str, request: Request) -> RedirectResponse:
    from urllib.parse import urlencode

    qp = dict(request.query_params)
    qp["role_id"] = role_id
    return RedirectResponse(url=f"/api/audit/groups?{urlencode(qp)}", status_code=307)


@router.get("/{role_id}/members", name="role_members_by_id")
def get_role_members(role_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    role = (
        db.query(RoleGroup)
        .filter(_db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        .first()
    )
    if role is None:
        raise HTTPException(404, "Not Found")
    mappings = (
        db.query(RoleGroupMap)
        .filter(RoleGroupMap.role_group_id == role.id)
        .filter(_db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > _db.func.now()))
        .all()
    )
    return {
        "groups": [m.group_id for m in mappings if not m.is_owner],
        "owner_groups": [m.group_id for m in mappings if m.is_owner],
    }


@router.put("/{role_id}/members", name="role_members_by_id_put")
def put_role_members(
    role_id: str,
    body: RoleMember,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    role = (
        db.query(RoleGroup)
        .filter(_db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        .first()
    )
    if role is None:
        raise HTTPException(404, "Not Found")
    if not can_manage_group(db, current_user_id, role) and not is_access_admin(db, current_user_id):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    ModifyRoleGroups(
        role_group=role,
        groups_to_add=body.groups_to_add,
        groups_to_remove=body.groups_to_remove,
        owner_groups_to_add=body.owner_groups_to_add,
        owner_groups_to_remove=body.owner_groups_to_remove,
        groups_added_ended_at=body.groups_added_ending_at,
        current_user_id=current_user_id,
        created_reason=body.created_reason or "",
    ).execute()
    return get_role_members(role_id, db, current_user_id)
