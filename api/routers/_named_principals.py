"""Resolve the user and role ids a request names into summaries for its response."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from sqlalchemy import select

from api.database import DbSession
from api.models import OktaUser, RoleGroup
from api.schemas import OktaUserSummary, RoleGroupRef


class _NamesPrincipals(Protocol):
    named_users: list[OktaUserSummary]
    named_roles: list[RoleGroupRef]


async def attach_named_principals[D: _NamesPrincipals](
    db: DbSession,
    details: Sequence[D],
    user_ids_of: Callable[[D], Iterable[str]],
    role_ids_of: Callable[[D], Iterable[str]],
) -> None:
    """Fill each detail's `named_users` / `named_roles` with two queries for the whole batch.

    Args:
        db: The session.
        details: The response models to fill.
        user_ids_of: The user ids a detail names, in display order.
        role_ids_of: The role ids a detail names, in display order.
    """
    wanted_users = [list(dict.fromkeys(user_ids_of(d))) for d in details]
    wanted_roles = [list(dict.fromkeys(role_ids_of(d))) for d in details]
    user_ids = {u for ids in wanted_users for u in ids}
    role_ids = {r for ids in wanted_roles for r in ids}
    users = (
        {
            u.id: OktaUserSummary.model_validate(u, from_attributes=True)
            for u in (
                await db.scalars(select(OktaUser).where(OktaUser.id.in_(user_ids)).where(OktaUser.deleted_at.is_(None)))
            ).all()
        }
        if user_ids
        else {}
    )
    roles = (
        {
            r.id: RoleGroupRef.model_validate(r, from_attributes=True)
            for r in (
                await db.scalars(
                    select(RoleGroup).where(RoleGroup.id.in_(role_ids)).where(RoleGroup.deleted_at.is_(None))
                )
            ).all()
        }
        if role_ids
        else {}
    )
    for d, user_list, role_list in zip(details, wanted_users, wanted_roles, strict=True):
        d.named_users = [users[u] for u in user_list if u in users]
        d.named_roles = [roles[r] for r in role_list if r in roles]
