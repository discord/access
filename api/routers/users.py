"""Users router. Endpoints:

  GET /api/users
  GET /api/users/{user_id}              (also accepts "@me")
  GET /api/users/{user_id}/audit         redirects to /api/audit/users
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic
from sqlalchemy.sql import sqltypes
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import (
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.pagination import paginate
from api.schemas import OktaUserOut, OktaUserSummary

router = APIRouter(prefix="/api/users", tags=["users"])

ALL_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup, RoleGroup])
_user_adapter = TypeAdapter(OktaUserOut)
_user_summary_adapter = TypeAdapter(OktaUserSummary)


@router.get("", name="users")
def list_users(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).order_by(func.lower(OktaUser.email))

    if q:
        like = f"%{q}%"
        query = query.filter(
            _db.or_(
                OktaUser.email.ilike(like),
                OktaUser.first_name.ilike(like),
                OktaUser.last_name.ilike(like),
                OktaUser.display_name.ilike(like),
                (OktaUser.first_name + " " + OktaUser.last_name).ilike(like),
                OktaUser.profile.cast(sqltypes.String).ilike(like),
            )
        )

    return paginate(request, query, _user_summary_adapter)


@router.get("/{user_id}", name="user_by_id")
def get_user(user_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    if user_id == "@me":
        user_id = current_user_id

    user = (
        db.query(OktaUser)
        .options(
            selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
            selectinload(OktaUser.active_group_memberships).options(
                joinedload(OktaUserGroupMember.active_group.of_type(ALL_GROUP_TYPES)).joinedload(
                    ALL_GROUP_TYPES.AppGroup.app
                ),
                joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
                    RoleGroupMap.active_role_group
                ),
            ),
            selectinload(OktaUser.active_group_ownerships).options(
                joinedload(OktaUserGroupMember.active_group.of_type(ALL_GROUP_TYPES)).options(
                    joinedload(ALL_GROUP_TYPES.AppGroup.app),
                    selectinload(ALL_GROUP_TYPES.active_group_tags).options(
                        joinedload(OktaGroupTagMap.active_tag),
                        joinedload(OktaGroupTagMap.active_app_tag_mapping),
                    ),
                ),
                joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
                    RoleGroupMap.active_role_group
                ),
            ),
            joinedload(OktaUser.manager),
        )
        .filter(_db.or_(OktaUser.id == user_id, OktaUser.email.ilike(user_id)))
        .order_by(nullsfirst(OktaUser.deleted_at.desc()))
        .first()
    )
    if user is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not Found")
    return _user_adapter.dump_python(_user_adapter.validate_python(user, from_attributes=True), mode="json")


@router.get("/{user_id}/audit", name="user_audit_by_id")
def get_user_audit(user_id: str, request: Request) -> RedirectResponse:
    qp = dict(request.query_params)
    qp["user_id"] = user_id
    from urllib.parse import urlencode

    return RedirectResponse(url=f"/api/audit/users?{urlencode(qp)}", status_code=307)
