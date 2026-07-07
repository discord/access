from typing import List

from sqlalchemy import func, or_, select

from api.extensions import db
from api.models.core_models import App, AppGroup, OktaGroup, OktaUser, OktaUserGroupMember


async def get_app_managers(app_id: str) -> List[OktaUser]:
    """Returns the users that can manage members of the app"""
    owner_app_groups_stmt = (
        select(AppGroup)
        .where(OktaGroup.deleted_at.is_(None))
        .where(AppGroup.app_id == app_id)
        .where(AppGroup.is_owner.is_(True))
    )

    if ((await db.session.scalar(select(func.count()).select_from(owner_app_groups_stmt.subquery()))) or 0) > 0:
        owner_app_group_ids = [ag.id for ag in await db.session.scalars(owner_app_groups_stmt)]
        result = await db.session.scalars(
            select(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .where(OktaUserGroupMember.group_id.in_(owner_app_group_ids))
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        )
        return list(result.all())

    return []


async def get_access_owners() -> List[OktaUser]:
    """Returns the access super admins that are members of the owners group"""

    access_app = (
        await db.session.scalars(
            select(App).where(App.deleted_at.is_(None)).where(App.name == App.ACCESS_APP_RESERVED_NAME)
        )
    ).first()

    if access_app is None:
        return []

    owner_app_groups_stmt = (
        select(AppGroup)
        .where(OktaGroup.deleted_at.is_(None))
        .where(AppGroup.app_id == access_app.id)
        .where(AppGroup.is_owner.is_(True))
    )

    if ((await db.session.scalar(select(func.count()).select_from(owner_app_groups_stmt.subquery()))) or 0) > 0:
        owner_app_group_ids = [ag.id for ag in await db.session.scalars(owner_app_groups_stmt)]
        result = await db.session.scalars(
            select(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .where(OktaUserGroupMember.group_id.in_(owner_app_group_ids))
            .where(OktaUserGroupMember.is_owner.is_(False))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        )
        return list(result.all())

    return []


def app_owners_group_description(app_name: str) -> str:
    return f"Owners of the {app_name} application"
