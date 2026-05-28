from typing import List

from sqlalchemy import func, or_

from api.extensions import db
from api.models.core_models import OktaUser, OktaUserGroupMember


def get_group_managers(group_id: str) -> List[OktaUser]:
    return (
        db.session.query(OktaUser)
        .join(OktaUserGroupMember, OktaUser.id == OktaUserGroupMember.user_id)
        .filter(OktaUserGroupMember.group_id == group_id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
        .all()
    )
