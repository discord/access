from typing import List

from sqlalchemy import func, or_, select

from api.extensions import db
from api.models.core_models import OktaUser, OktaUserGroupMember


def get_group_managers(group_id: str) -> List[OktaUser]:
    return list(
        db.session.scalars(
            select(OktaUser)
            .join(OktaUserGroupMember, OktaUser.id == OktaUserGroupMember.user_id)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        ).all()
    )
