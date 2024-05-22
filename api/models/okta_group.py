from typing import List

from api.extensions import db
from api.models.core_models import OktaUser, OktaUserGroupMember


def get_group_managers(group_id: str) -> List[OktaUser]:
    return (
        OktaUser.query.join(OktaUserGroupMember, OktaUser.id == OktaUserGroupMember.user_id)
        .filter(OktaUserGroupMember.group_id == group_id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        )
        .all()
    )
