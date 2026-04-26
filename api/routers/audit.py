"""Audit router. Provides:

  GET /api/audit/users    — user/group membership audit
  GET /api/audit/groups   — group/role mapping audit
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import OktaUserGroupMember, RoleGroupMap
from api.pagination import paginate

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/users", name="users_and_groups")
def users_and_groups(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = db.query(OktaUserGroupMember).order_by(OktaUserGroupMember.created_at.desc())

    user_id = request.query_params.get("user_id")
    group_id = request.query_params.get("group_id")
    if user_id:
        query = query.filter(OktaUserGroupMember.user_id == user_id)
    if group_id:
        query = query.filter(OktaUserGroupMember.group_id == group_id)

    def _serialize(m: Any) -> dict[str, Any]:
        return {
            "id": m.id,
            "user_id": m.user_id,
            "group_id": m.group_id,
            "is_owner": m.is_owner,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
            "created_reason": m.created_reason or "",
            "should_expire": getattr(m, "should_expire", None),
        }

    return paginate(request, query, _serialize)


@router.get("/groups", name="groups_and_roles")
def groups_and_roles(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    query = db.query(RoleGroupMap).order_by(RoleGroupMap.created_at.desc())

    role_id = request.query_params.get("role_id")
    group_id = request.query_params.get("group_id")
    if role_id:
        query = query.filter(RoleGroupMap.role_group_id == role_id)
    if group_id:
        query = query.filter(RoleGroupMap.group_id == group_id)

    def _serialize(m: Any) -> dict[str, Any]:
        return {
            "id": getattr(m, "id", None),
            "role_group_id": m.role_group_id,
            "group_id": m.group_id,
            "is_owner": m.is_owner,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
        }

    return paginate(request, query, _serialize)
