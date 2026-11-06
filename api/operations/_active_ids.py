"""Validate the user and role ids a request names, and read back what was granted."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select

from api.exceptions import InvalidRequestError
from api.extensions import db
from api.models import OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap


async def require_active_user_ids(user_ids: list[str], label: str) -> list[str]:
    """Return the distinct ids in `user_ids`, in order.

    Raises:
        InvalidRequestError: An id does not name an active user. `label` names
            the field in the message.
    """
    wanted = list(dict.fromkeys(user_ids))
    if not wanted:
        return []
    found = set(
        (
            await db.session.scalars(
                select(OktaUser.id).where(OktaUser.id.in_(wanted)).where(OktaUser.deleted_at.is_(None))
            )
        ).all()
    )
    missing = [u for u in wanted if u not in found]
    if missing:
        raise InvalidRequestError(f"Unknown {label} user ids: {', '.join(missing)}")
    return wanted


async def require_active_role_ids(role_ids: list[str], label: str) -> list[str]:
    """Return the distinct ids in `role_ids`, in order.

    Raises:
        InvalidRequestError: An id does not name an active role. `label` names
            the field in the message.
    """
    wanted = list(dict.fromkeys(role_ids))
    if not wanted:
        return []
    found = set(
        (
            await db.session.scalars(
                select(RoleGroup.id).where(RoleGroup.id.in_(wanted)).where(RoleGroup.deleted_at.is_(None))
            )
        ).all()
    )
    missing = [r for r in wanted if r not in found]
    if missing:
        raise InvalidRequestError(f"Unknown {label} role ids: {', '.join(missing)}")
    return wanted


@dataclass
class GrantedAccess:
    """The active direct user grants and role grants on a group."""

    owner_user_ids: set[str]
    member_user_ids: set[str]
    owner_role_ids: set[str]
    member_role_ids: set[str]


async def granted_access(group_id: str) -> GrantedAccess:
    """Read the active direct user grants (not those through a role) and role grants on `group_id`."""
    user_rows = (
        await db.session.execute(
            select(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.role_group_map_id.is_(None))
            .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
        )
    ).all()
    role_rows = (
        await db.session.execute(
            select(RoleGroupMap.role_group_id, RoleGroupMap.is_owner)
            .where(RoleGroupMap.group_id == group_id)
            .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
        )
    ).all()
    return GrantedAccess(
        owner_user_ids={u for u, is_owner in user_rows if is_owner},
        member_user_ids={u for u, is_owner in user_rows if not is_owner},
        owner_role_ids={r for r, is_owner in role_rows if is_owner},
        member_role_ids={r for r, is_owner in role_rows if not is_owner},
    )
