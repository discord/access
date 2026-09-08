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
