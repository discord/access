from typing import List

from sqlalchemy import func, or_, select

from api.extensions import db
from api.models.core_models import OktaUser, OktaUserGroupMember


async def get_group_managers(group_id: str) -> List[OktaUser]:
    # A user can hold two concurrent active OktaUserGroupMember owner rows for
    # the same group at once (a direct grant plus one via a role mapping,
    # per the access-grant-precedence rules) -- distinct() so such a user is
    # returned once, not once per row.
    result = await db.session.scalars(
        select(OktaUser)
        .distinct()
        .join(OktaUserGroupMember, OktaUser.id == OktaUserGroupMember.user_id)
        .where(OktaUserGroupMember.group_id == group_id)
        .where(OktaUserGroupMember.is_owner.is_(True))
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
    )
    return list(result.all())
