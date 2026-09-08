"""Detect and remove direct group grants that a role already provides.

Access prefers role-based access: a role attached to a group grants that group's
access to every member of the role. A direct grant covering the same access is
redundant twice over -- it obscures why a user has access, and it keeps that
access alive after the user leaves the role, so role membership stops being a
reliable lever.

This module finds those redundant direct grants and, when asked to, removes
them, leaving the role as the single legible source of the access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from api.extensions import db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember


class AccessTarget(Enum):
    """Which dimension(s) of direct grant a prune run considers.

    The values match the strings the `--target` CLI option accepts, so the
    command converts its raw option value straight into this enum.
    """

    MEMBERS = "members"
    OWNERS = "owners"
    BOTH = "both"

    def is_owner_values(self) -> tuple[bool, ...]:
        """The `OktaUserGroupMember.is_owner` values this target covers.

        Returns:
            A tuple suitable for an `is_owner.in_(...)` filter.
        """
        if self is AccessTarget.MEMBERS:
            return (False,)
        if self is AccessTarget.OWNERS:
            return (True,)
        return (False, True)


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    """The later of two end dates, reading `None` as indefinite.

    Indefinite access outlives every concrete date, so a `None` on either side
    wins.
    """
    if a is None or b is None:
        return None
    return max(a, b)


@dataclass(frozen=True)
class RedundantGrant:
    """One (user, group, dimension) triple holding both a direct grant and
    active role-derived coverage.

    The two end dates are aggregated across every active row on their side of
    the triple, with `None` meaning indefinite. They are aggregated rather than
    compared row by row because `ModifyGroupUsers` ends *every* direct row for a
    user, group, and dimension in one statement: the decision has to be
    all-or-nothing per triple, or acting on one row could destroy a sibling that
    would have failed the guard.

    Attributes:
        user_id: The user holding both grants.
        user_email: That user's email, carried for reporting.
        group_id: The group both grants target.
        group_name: That group's name, carried for reporting.
        is_owner: False for membership, True for ownership.
        latest_direct_ended_at: Latest end date across the direct rows.
        latest_role_ended_at: Latest end date across the role-derived rows.
    """

    user_id: str
    user_email: str
    group_id: str
    group_name: str
    is_owner: bool
    latest_direct_ended_at: datetime | None
    latest_role_ended_at: datetime | None

    @property
    def shortens_access(self) -> bool:
        """Whether removing the direct grant would end this user's access to
        this group sooner than it otherwise would.

        Returns:
            True when the direct grant outlives the role coverage.
        """
        if self.latest_role_ended_at is None:
            return False
        if self.latest_direct_ended_at is None:
            return True
        return self.latest_direct_ended_at > self.latest_role_ended_at


def _active(model: Any) -> Any:
    """SQL predicate matching membership rows that have not ended.

    Takes the model or alias to filter, so the same predicate applies to both
    sides of the direct/role-derived self-join.
    """
    return or_(model.ended_at.is_(None), model.ended_at > func.now())


async def find_redundant_grants(
    *,
    target: AccessTarget,
    group_ids: set[str] | None = None,
    user_ids: set[str] | None = None,
) -> list[RedundantGrant]:
    """Find every (user, group, dimension) triple holding both an active direct
    grant and active role-derived coverage.

    Args:
        target: Which dimension(s) of direct grant to consider.
        group_ids: Restrict to these groups, or None for every group.
        user_ids: Restrict to these users, or None for every user.

    Returns:
        The candidates, ordered by group name, then user email, then dimension.
        Membership rows sort before ownership rows within a group and user.
    """
    derived = aliased(OktaUserGroupMember)
    direct_stmt = (
        select(
            OktaUserGroupMember.user_id,
            OktaUser.email,
            OktaUserGroupMember.group_id,
            OktaGroup.name,
            OktaUserGroupMember.is_owner,
            OktaUserGroupMember.ended_at,
        )
        .join(OktaUser, OktaUser.id == OktaUserGroupMember.user_id)
        .join(OktaGroup, OktaGroup.id == OktaUserGroupMember.group_id)
        .where(OktaUser.deleted_at.is_(None))
        .where(OktaGroup.deleted_at.is_(None))
        .where(_active(OktaUserGroupMember))
        .where(OktaUserGroupMember.role_group_map_id.is_(None))
        .where(OktaUserGroupMember.is_owner.in_(target.is_owner_values()))
        # Pair each direct grant with role-derived coverage in the database, so
        # only genuine candidates cross the wire rather than every active
        # membership in the system.
        .where(
            select(1)
            .where(derived.user_id == OktaUserGroupMember.user_id)
            .where(derived.group_id == OktaUserGroupMember.group_id)
            .where(derived.is_owner == OktaUserGroupMember.is_owner)
            .where(derived.role_group_map_id.isnot(None))
            .where(_active(derived))
            .exists()
        )
    )
    if group_ids is not None:
        direct_stmt = direct_stmt.where(OktaUserGroupMember.group_id.in_(group_ids))
    if user_ids is not None:
        direct_stmt = direct_stmt.where(OktaUserGroupMember.user_id.in_(user_ids))

    # Aggregation happens here rather than in SQL: `bool_or` is Postgres-only
    # and the suite also runs on SQLite, where `max()` over a DateTime column
    # loses the type and hands back a string.
    direct_latest: dict[tuple[str, str, bool], datetime | None] = {}
    identities: dict[tuple[str, str, bool], tuple[str, str]] = {}
    for user_id, user_email, group_id, group_name, is_owner, ended_at in (await db.session.execute(direct_stmt)).all():
        key = (user_id, group_id, is_owner)
        identities[key] = (user_email, group_name)
        # Keyed on presence, not truthiness: a stored None means indefinite and
        # must not read as "nothing recorded yet".
        direct_latest[key] = _later(direct_latest[key], ended_at) if key in direct_latest else ended_at

    if len(direct_latest) == 0:
        return []

    role_stmt = (
        select(
            OktaUserGroupMember.user_id,
            OktaUserGroupMember.group_id,
            OktaUserGroupMember.is_owner,
            OktaUserGroupMember.ended_at,
        )
        .where(_active(OktaUserGroupMember))
        .where(OktaUserGroupMember.role_group_map_id.isnot(None))
        .where(OktaUserGroupMember.user_id.in_({key[0] for key in direct_latest}))
        .where(OktaUserGroupMember.group_id.in_({key[1] for key in direct_latest}))
    )
    role_latest: dict[tuple[str, str, bool], datetime | None] = {}
    for user_id, group_id, is_owner, ended_at in (await db.session.execute(role_stmt)).all():
        key = (user_id, group_id, is_owner)
        # The user/group pairs are filtered as a cross product, so this query can
        # return triples that were never candidates.
        if key not in direct_latest:
            continue
        role_latest[key] = _later(role_latest[key], ended_at) if key in role_latest else ended_at

    grants = []
    for key, latest_direct in direct_latest.items():
        user_id, group_id, is_owner = key
        user_email, group_name = identities[key]
        # The EXISTS clause above guarantees coverage for every candidate.
        assert key in role_latest
        grants.append(
            RedundantGrant(
                user_id=user_id,
                user_email=user_email,
                group_id=group_id,
                group_name=group_name,
                is_owner=is_owner,
                latest_direct_ended_at=latest_direct,
                latest_role_ended_at=role_latest[key],
            )
        )
    return sorted(grants, key=lambda g: (g.group_name, g.user_email, g.is_owner))
