"""
App group helper functions for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

from typing import List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from api_v2.models.core_models import App, AppGroup, OktaGroup, OktaUser, OktaUserGroupMember


def get_app_managers(db: Session, app_id: str) -> List[OktaUser]:
    """Returns the users that can manage members of the app"""
    owner_app_groups = (
        db.query(AppGroup)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(AppGroup.app_id == app_id)
        .filter(AppGroup.is_owner.is_(True))
        .all()
    )

    if owner_app_groups:
        return (
            db.query(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .all()
        )

    return []


def get_access_owners(db: Session) -> List[OktaUser]:
    """Returns the access super admins that are members of the owners group"""

    access_app = (
        db.query(App)
        .filter(App.deleted_at.is_(None))
        .filter(App.name == App.ACCESS_APP_RESERVED_NAME)
        .first()
    )

    if not access_app:
        return []

    owner_app_groups = (
        db.query(AppGroup)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(AppGroup.app_id == access_app.id)
        .filter(AppGroup.is_owner.is_(True))
        .all()
    )

    if owner_app_groups:
        return (
            db.query(OktaUser)
            .join(OktaUser.all_group_memberships_and_ownerships)
            .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
            .filter(OktaUserGroupMember.is_owner.is_(False))
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .all()
        )

    return []


def app_owners_group_description(app_name: str) -> str:
    """Returns a standard description for app owners groups"""
    return f"Owners of the {app_name} application"