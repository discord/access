"""Authorization helpers and FastAPI `Depends` factories.

The bare `is_*` / `can_*` functions take an explicit `(db, current_user_id)`
so they can be called from anywhere; the `require_*` factories are FastAPI
parameter dependencies that raise HTTPException(403) on failure (and 404
if the target object isn't found).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth.dependencies import CurrentUserId
from api.config import settings
from api.database import DbSession
from api.models import App, AppGroup, OktaGroup, OktaUserGroupMember


async def is_group_owner(db: AsyncSession, current_user_id: str, group: OktaGroup) -> bool:
    stmt = (
        select(OktaUserGroupMember)
        .where(OktaUserGroupMember.group_id == group.id)
        .where(OktaUserGroupMember.user_id == current_user_id)
        .where(OktaUserGroupMember.is_owner.is_(True))
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
    )
    return (await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0) > 0


async def is_app_owner_group_owner(
    db: AsyncSession,
    current_user_id: str,
    *,
    # Any group may be passed (callers hold an `OktaGroup`); the `type(...) is
    # AppGroup` guard below returns False for non-app groups.
    app_group: Optional[OktaGroup] = None,
    app: Optional[App] = None,
) -> bool:
    if app is not None:
        app_id = app.id
    elif app_group is not None and type(app_group) is AppGroup:
        app_id = app_group.app_id
    else:
        return False

    owner_app_groups_stmt = (
        select(AppGroup)
        .where(OktaGroup.deleted_at.is_(None))
        .where(AppGroup.app_id == app_id)
        .where(AppGroup.is_owner.is_(True))
    )
    if (await db.scalar(select(func.count()).select_from(owner_app_groups_stmt.subquery())) or 0) == 0:
        return False

    owner_app_groups = (await db.scalars(owner_app_groups_stmt)).all()
    stmt = (
        select(OktaUserGroupMember)
        .where(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
        .where(OktaUserGroupMember.user_id == current_user_id)
        .where(OktaUserGroupMember.is_owner.is_(True))
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
    )
    return (await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0) > 0


async def is_access_admin(db: AsyncSession, current_user_id: str) -> bool:
    access_app = (
        await db.scalars(
            select(App)
            .options(selectinload(App.active_owner_app_groups))
            .where(App.deleted_at.is_(None))
            .where(App.name == App.ACCESS_APP_RESERVED_NAME)
        )
    ).first()
    if access_app is None or len(access_app.active_owner_app_groups) == 0:
        return False
    stmt = (
        select(OktaUserGroupMember)
        .where(OktaUserGroupMember.group_id.in_([ag.id for ag in access_app.active_owner_app_groups]))
        .where(OktaUserGroupMember.user_id == current_user_id)
        .where(OktaUserGroupMember.is_owner.is_(False))
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
    )
    return (await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0) > 0


async def can_manage_group(db: AsyncSession, current_user_id: str, group: OktaGroup) -> bool:
    if await is_group_owner(db, current_user_id, group):
        return True
    if await is_app_owner_group_owner(db, current_user_id, app_group=group):
        return True
    if await is_access_admin(db, current_user_id):
        return True
    return False


async def can_delete_group(db: AsyncSession, current_user_id: str, group: OktaGroup) -> bool:
    if current_user_id in settings.app_group_deleter_ids and type(group) is AppGroup and group.is_managed:
        return True
    return await can_manage_group(db, current_user_id, group)


# --- Depends factories -----------------------------------------------------


async def require_access_admin(
    db: DbSession,
    current_user_id: CurrentUserId,
) -> str:
    if not await is_access_admin(db, current_user_id):
        raise HTTPException(status_code=403, detail="Current user is not allowed to perform this action")
    return current_user_id


async def require_access_admin_or_app_creator(
    db: DbSession,
    current_user_id: CurrentUserId,
) -> str:
    if current_user_id in settings.app_creator_ids:
        return current_user_id
    if await is_access_admin(db, current_user_id):
        return current_user_id
    raise HTTPException(status_code=403, detail="Current user is not allowed to perform this action")


async def require_app_owner_or_access_admin_for_app(
    app_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> App:
    app_obj = (
        await db.scalars(select(App).where(App.deleted_at.is_(None)).where(or_(App.id == app_id, App.name == app_id)))
    ).first()
    if app_obj is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if await is_app_owner_group_owner(db, current_user_id, app=app_obj):
        return app_obj
    if await is_access_admin(db, current_user_id):
        return app_obj
    raise HTTPException(status_code=403, detail="Current user is not allowed to perform this action")


# Convenience type aliases for parameter declaration
AppForOwner = Annotated[App, Depends(require_app_owner_or_access_admin_for_app)]
