"""Audit router. Provides:

  GET /api/audit/users    — user/group membership audit
  GET /api/audit/groups   — group/role mapping audit
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.pagination import paginate

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _validate_user(db: Any, user_id: str | None) -> None:
    if user_id is None:
        return
    obj = (
        db.query(OktaUser)
        .filter(OktaUser.id == user_id)
        .filter(OktaUser.deleted_at.is_(None))
        .first()
    )
    if obj is None:
        raise HTTPException(404, "Not Found")


def _validate_group(db: Any, group_id: str | None) -> None:
    if group_id is None:
        return
    obj = (
        db.query(OktaGroup)
        .filter(OktaGroup.id == group_id)
        .filter(OktaGroup.deleted_at.is_(None))
        .first()
    )
    if obj is None:
        raise HTTPException(404, "Not Found")


def _validate_role(db: Any, role_id: str | None) -> None:
    if role_id is None:
        return
    obj = (
        db.query(RoleGroup)
        .filter(RoleGroup.id == role_id)
        .filter(RoleGroup.deleted_at.is_(None))
        .first()
    )
    if obj is None:
        raise HTTPException(404, "Not Found")


@router.get("/users", name="users_and_groups")
def users_and_groups(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    user_id = request.query_params.get("user_id")
    group_id = request.query_params.get("group_id")
    _validate_user(db, user_id)
    _validate_group(db, group_id)

    from sqlalchemy.orm import joinedload

    query = (
        db.query(OktaUserGroupMember)
        .options(
            joinedload(OktaUserGroupMember.user),
            joinedload(OktaUserGroupMember.group),
        )
        .join(OktaUserGroupMember.user)
        .join(OktaUserGroupMember.group)
        .order_by(OktaUserGroupMember.created_at.desc())
    )
    if user_id:
        query = query.filter(OktaUserGroupMember.user_id == user_id)
    if group_id:
        query = query.filter(OktaUserGroupMember.group_id == group_id)

    q = request.query_params.get("q")
    if q:
        like = f"%{q}%"
        # Search by group name when filtering by user; by user email/name
        # when filtering by group; by both otherwise.
        query = query.filter(
            _db.or_(
                OktaGroup.name.ilike(like),
                OktaUser.email.ilike(like),
                OktaUser.first_name.ilike(like),
                OktaUser.last_name.ilike(like),
                OktaUser.display_name.ilike(like),
            )
        )

    owner_filter = request.query_params.get("owner")
    if owner_filter is not None and owner_filter != "":
        query = query.filter(OktaUserGroupMember.is_owner == (owner_filter.lower() == "true"))

    active_filter = request.query_params.get("active")
    if active_filter is not None and active_filter != "":
        if active_filter.lower() == "true":
            query = query.filter(
                _db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > _db.func.now(),
                )
            )
        else:
            query = query.filter(OktaUserGroupMember.ended_at <= _db.func.now())

    needs_review = request.query_params.get("needs_review")
    if needs_review is not None and needs_review != "":
        query = query.filter(OktaUserGroupMember.should_expire == (needs_review.lower() == "true"))

    def _serialize(m: Any) -> dict[str, Any]:
        return {
            "id": m.id,
            "user_id": m.user_id,
            "group_id": m.group_id,
            "role_group_map_id": getattr(m, "role_group_map_id", None),
            "is_owner": m.is_owner,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
            "created_reason": m.created_reason or "",
            "should_expire": getattr(m, "should_expire", None),
        }

    return paginate(request, query, _serialize)


@router.get("/groups", name="groups_and_roles")
def groups_and_roles(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    from sqlalchemy.orm import aliased

    role_id = request.query_params.get("role_id")
    group_id = request.query_params.get("group_id")
    _validate_role(db, role_id)
    _validate_group(db, group_id)

    associated_group = aliased(OktaGroup)
    query = (
        db.query(RoleGroupMap)
        .join(associated_group, RoleGroupMap.group_id == associated_group.id)
        .order_by(RoleGroupMap.created_at.desc())
    )
    if role_id:
        query = query.filter(RoleGroupMap.role_group_id == role_id)
    if group_id:
        query = query.filter(RoleGroupMap.group_id == group_id)

    owner_filter = request.query_params.get("owner")
    if owner_filter is not None and owner_filter != "":
        query = query.filter(RoleGroupMap.is_owner == (owner_filter.lower() == "true"))

    q = request.query_params.get("q")
    if q:
        like = f"%{q}%"
        query = query.filter(associated_group.name.ilike(like))

    def _serialize(m: Any) -> dict[str, Any]:
        return {
            "id": getattr(m, "id", None),
            "role_group_id": m.role_group_id,
            "group_id": m.group_id,
            "is_owner": m.is_owner,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if hasattr(m, "updated_at") and m.updated_at else None,
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
        }

    return paginate(request, query, _serialize)
