"""
OktaGroup helper functions for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

from typing import List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from api_v2.models.core_models import OktaUser, OktaUserGroupMember


def get_group_managers(db: Session, group_id: str) -> List[OktaUser]:
    """Returns the users that are owners of a specific group"""
    return (
        db.query(OktaUser)
        .join(OktaUserGroupMember, OktaUser.id == OktaUserGroupMember.user_id)
        .filter(OktaUserGroupMember.group_id == group_id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        )
        .all()
    )