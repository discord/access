"""Authorization helpers and FastAPI `Depends` factories.

The bare `is_*` / `can_*` functions take an explicit `(db, current_user_id)`
so they can be called from anywhere; the `require_*` factories are FastAPI
parameter dependencies that raise HTTPException(403) on failure (and 404
if the target object isn't found).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from api.auth.dependencies import CurrentUserId
from api.config import settings
from api.database import DbSession
from api.extensions import db as _db_shim
from api.models import App, AppGroup, OktaGroup, OktaUserGroupMember


def is_group_owner(db: Session, current_user_id: str, group: OktaGroup) -> bool:
    return (
        db.query(OktaUserGroupMember)
        .filter(OktaUserGroupMember.group_id == group.id)
        .filter(OktaUserGroupMember.user_id == current_user_id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > _db_shim.func.now(),
            )
        )
        .count()
        > 0
    )


def is_app_owner_group_owner(
    db: Session,
    current_user_id: str,
    *,
    app_group: Optional[AppGroup] = None,
    app: Optional[App] = None,
) -> bool:
    if app is not None:
        app_id = app.id
    elif app_group is not None and type(app_group) is AppGroup:
        app_id = app_group.app_id
    else:
        return False

    owner_app_groups = (
        db.query(AppGroup)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(AppGroup.app_id == app_id)
        .filter(AppGroup.is_owner.is_(True))
    )
    if owner_app_groups.count() == 0:
        return False

    return (
        db.query(OktaUserGroupMember)
        .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
        .filter(OktaUserGroupMember.user_id == current_user_id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > _db_shim.func.now(),
            )
        )
        .count()
        > 0
    )


def is_access_admin(db: Session, current_user_id: str) -> bool:
    access_app = (
        db.query(App)
        .options(selectinload(App.active_owner_app_groups))
        .filter(App.deleted_at.is_(None))
        .filter(App.name == App.ACCESS_APP_RESERVED_NAME)
        .first()
    )
    if access_app is None or len(access_app.active_owner_app_groups) == 0:
        return False
    return (
        db.query(OktaUserGroupMember)
        .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in access_app.active_owner_app_groups]))
        .filter(OktaUserGroupMember.user_id == current_user_id)
        .filter(OktaUserGroupMember.is_owner.is_(False))
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > _db_shim.func.now(),
            )
        )
        .count()
        > 0
    )


def can_manage_group(db: Session, current_user_id: str, group: OktaGroup) -> bool:
    if is_group_owner(db, current_user_id, group):
        return True
    if is_app_owner_group_owner(db, current_user_id, app_group=group):
        return True
    if is_access_admin(db, current_user_id):
        return True
    return False


# --- Depends factories -----------------------------------------------------


def require_access_admin(
    db: DbSession,
    current_user_id: CurrentUserId,
) -> str:
    if not is_access_admin(db, current_user_id):
        raise HTTPException(status_code=403, detail="Current user is not allowed to perform this action")
    return current_user_id


def require_access_admin_or_app_creator(
    db: DbSession,
    current_user_id: CurrentUserId,
) -> str:
    if settings.APP_CREATOR_ID is not None and current_user_id == settings.APP_CREATOR_ID:
        return current_user_id
    if is_access_admin(db, current_user_id):
        return current_user_id
    raise HTTPException(status_code=403, detail="Current user is not allowed to perform this action")


def require_app_owner_or_access_admin_for_app(
    app_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> App:
    app_obj = db.query(App).filter(App.deleted_at.is_(None)).filter(or_(App.id == app_id, App.name == app_id)).first()
    if app_obj is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if is_app_owner_group_owner(db, current_user_id, app=app_obj):
        return app_obj
    if is_access_admin(db, current_user_id):
        return app_obj
    raise HTTPException(status_code=403, detail="Current user is not allowed to perform this action")


# Convenience type aliases for parameter declaration
AppForOwner = Annotated[App, Depends(require_app_owner_or_access_admin_for_app)]
