from sqlalchemy import func, or_, select

from api.extensions import db
from api.models.core_models import OktaGroup, OktaUser, OktaUserGroupMember


async def get_group_managers(group_id: str) -> list[OktaUser]:
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


async def is_group_name_taken(name: str) -> bool:
    """Report whether a live group already uses ``name``.

    Group names are unique case-insensitively among groups that are not
    soft-deleted, so ``"Platform"`` is taken by an existing ``"platform"``.

    Args:
        name: The full group name, including any ``App-``/``Role-`` prefix.

    Returns:
        True if a non-deleted group of any type has this name, ignoring case.
    """
    existing = await db.session.scalar(
        select(OktaGroup.id)
        .where(func.lower(OktaGroup.name) == func.lower(name))
        .where(OktaGroup.deleted_at.is_(None))
        .limit(1)
    )
    return existing is not None
