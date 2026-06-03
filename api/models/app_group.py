from typing import List

from sqlalchemy import func, or_

from api.extensions import db
from api.models.core_models import App, AppGroup, OktaGroup, OktaUser, OktaUserGroupMember


def get_app_managers(app_id: str) -> List[OktaUser]:
    """Returns the users that can manage members of the app"""
    owner_app_groups = (
        db.session.query(AppGroup)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(AppGroup.app_id == app_id)
        .filter(AppGroup.is_owner.is_(True))
    )

    if owner_app_groups.count() > 0:
        return (
            db.session.query(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .all()
        )

    return []


def get_access_owners() -> List[OktaUser]:
    """Returns the access super admins that are members of the owners group"""

    access_app = (
        db.session.query(App).filter(App.deleted_at.is_(None)).filter(App.name == App.ACCESS_APP_RESERVED_NAME).first()
    )

    if access_app is None:
        return []

    owner_app_groups = (
        db.session.query(AppGroup)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(AppGroup.app_id == access_app.id)
        .filter(AppGroup.is_owner.is_(True))
    )

    if owner_app_groups.count() > 0:
        return (
            db.session.query(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
            .filter(OktaUserGroupMember.is_owner.is_(False))
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .all()
        )

    return []


def app_owners_group_description(app_name: str) -> str:
    return f"Owners of the {app_name} application"
