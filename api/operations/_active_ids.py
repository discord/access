"""Validate user and role ids named in a request body."""

from __future__ import annotations

from sqlalchemy import select

from api.exceptions import InvalidRequestError
from api.extensions import db
from api.models import OktaUser, RoleGroup


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
