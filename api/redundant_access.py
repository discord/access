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

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from api.extensions import db
from api.models import App, AppGroup, OktaGroup, OktaUser, OktaUserGroupMember
from api.operations import ModifyGroupUsers

logger = logging.getLogger(__name__)


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
        # return triples that were never candidates. Keeping them out holds
        # `role_latest` to the candidate set; the loop below reads it only at
        # keys drawn from `direct_latest`, so a non-candidate entry would be
        # ignored rather than wrong.
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


class FilterResolutionError(ValueError):
    """A `--group`, `--user`, or `--app` filter value matched no active record.

    Raised rather than silently narrowing to nothing: a mistyped name would
    otherwise report as a clean sweep with nothing to prune.
    """


async def resolve_group_ids(group_filters: Sequence[str], app_filters: Sequence[str]) -> set[str] | None:
    """Resolve group and app filters to the set of group ids they name.

    Group filters match a group id or exact name. App filters match an app id or
    exact name and expand to that app's active app groups. The two kinds union. A
    filter value that matches more than one active record resolves to an
    arbitrary single one of them, since the lookup has no `ORDER BY` and
    `db.session.scalar()` returns whichever matching row comes back first.

    Args:
        group_filters: Group ids or exact names.
        app_filters: App ids or exact names.

    Returns:
        The group ids to restrict a sweep to, or None when neither filter was
        supplied, meaning do not narrow by group. An empty set means the filters
        resolved but name no groups.

    Raises:
        FilterResolutionError: A value matched no active group or app.
    """
    if len(group_filters) == 0 and len(app_filters) == 0:
        return None

    group_ids: set[str] = set()
    for value in group_filters:
        group_id = await db.session.scalar(
            select(OktaGroup.id)
            .where(OktaGroup.deleted_at.is_(None))
            .where(or_(OktaGroup.id == value, OktaGroup.name == value))
        )
        if group_id is None:
            raise FilterResolutionError(f"No active group matches {value!r}")
        group_ids.add(group_id)

    for value in app_filters:
        app_id = await db.session.scalar(
            select(App.id).where(App.deleted_at.is_(None)).where(or_(App.id == value, App.name == value))
        )
        if app_id is None:
            raise FilterResolutionError(f"No active app matches {value!r}")
        group_ids.update(
            (
                await db.session.scalars(
                    select(AppGroup.id).where(AppGroup.deleted_at.is_(None)).where(AppGroup.app_id == app_id)
                )
            ).all()
        )

    return group_ids


async def resolve_user_ids(user_filters: Sequence[str]) -> set[str] | None:
    """Resolve user filters to the set of user ids they name.

    Matches a user id or email; email comparison is case-insensitive, matching
    how the `init-builtin-apps` command resolves its admin. Email filters are
    matched via ILIKE, so `_` and `%` in the filter value act as SQL LIKE
    wildcards. A filter value that matches more than one active user resolves to
    an arbitrary single one of them, since the lookup has no `ORDER BY` and
    `db.session.scalar()` returns whichever matching row comes back first.

    Args:
        user_filters: User ids or emails.

    Returns:
        The user ids to restrict a sweep to, or None when no filter was supplied.

    Raises:
        FilterResolutionError: A value matched no active user.
    """
    if len(user_filters) == 0:
        return None

    user_ids: set[str] = set()
    for value in user_filters:
        user_id = await db.session.scalar(
            select(OktaUser.id)
            .where(OktaUser.deleted_at.is_(None))
            # Email matching is LIKE-based, consistent with the rest of the codebase,
            # so `_` and `%` in a filter value act as wildcards.
            .where(or_(OktaUser.id == value, OktaUser.email.ilike(value)))
        )
        if user_id is None:
            raise FilterResolutionError(f"No active user matches {value!r}")
        user_ids.add(user_id)

    return user_ids


class PruneOutcome(Enum):
    """What a prune run decided about one candidate.

    The values double as the labels the CLI prints.
    """

    REMOVED = "REMOVE"
    SKIPPED_WOULD_SHORTEN = "SKIP (would shorten)"
    FAILED = "FAILED"


@dataclass(frozen=True)
class GrantDecision:
    """One candidate and what the run decided about it."""

    grant: RedundantGrant
    outcome: PruneOutcome


@dataclass
class PruneSummary:
    """The outcome of one prune run, in reporting order.

    On a dry run, `removed` counts what an applying run would have removed.
    """

    decisions: list[GrantDecision] = field(default_factory=list)

    def _count(self, outcome: PruneOutcome) -> int:
        return sum(1 for decision in self.decisions if decision.outcome is outcome)

    @property
    def candidates(self) -> int:
        """How many redundant grants the run found."""
        return len(self.decisions)

    @property
    def removed(self) -> int:
        """How many direct grants were removed."""
        return self._count(PruneOutcome.REMOVED)

    @property
    def skipped(self) -> int:
        """How many candidates the duration guard held back."""
        return self._count(PruneOutcome.SKIPPED_WOULD_SHORTEN)

    @property
    def failed(self) -> int:
        """How many candidates were left in place by a failed write."""
        return self._count(PruneOutcome.FAILED)


async def prune_redundant_direct_access(
    *,
    target: AccessTarget = AccessTarget.BOTH,
    dry_run: bool = True,
    allow_shortening: bool = False,
    group_filters: Sequence[str] = (),
    user_filters: Sequence[str] = (),
    app_filters: Sequence[str] = (),
) -> PruneSummary:
    """Remove direct group grants that a role already provides to the same user.

    Args:
        target: Which dimension(s) of direct grant to prune.
        dry_run: When True, decide and report without writing anything.
        allow_shortening: When True, also remove direct grants that outlive their
            role coverage, ending the user's access sooner than it would have.
        group_filters: Group ids or exact names to restrict the sweep to.
        user_filters: User ids or emails to restrict the sweep to.
        app_filters: App ids or exact names whose app groups to restrict to.

    Returns:
        The decision for every candidate, ordered by group name, user email, and
        dimension.

    Raises:
        FilterResolutionError: A filter value matched no active record. Raised
            before any write, so a mistyped filter changes nothing.
    """
    group_ids = await resolve_group_ids(group_filters, app_filters)
    user_ids = await resolve_user_ids(user_filters)
    grants = await find_redundant_grants(target=target, group_ids=group_ids, user_ids=user_ids)

    summary = PruneSummary()
    by_group: dict[str, list[RedundantGrant]] = {}
    for grant in grants:
        if grant.shortens_access and not allow_shortening:
            summary.decisions.append(GrantDecision(grant, PruneOutcome.SKIPPED_WOULD_SHORTEN))
            continue
        by_group.setdefault(grant.group_id, []).append(grant)

    if dry_run:
        for group_grants in by_group.values():
            summary.decisions.extend(GrantDecision(g, PruneOutcome.REMOVED) for g in group_grants)
        summary.decisions.sort(key=_decision_sort_key)
        return summary

    for group_id, group_grants in by_group.items():
        # `grants` holds plain values, never ORM instances: a failed group's
        # rollback expires the whole identity map, and re-reading an expired
        # attribute on an AsyncSession raises MissingGreenlet rather than
        # refreshing. Holding only scalars keeps the loop independent of that.
        try:
            # ModifyGroupUsers ends only rows with a null `role_group_map_id`,
            # so this touches the direct grants and leaves role-derived coverage
            # intact. It commits its own unit of work.
            await ModifyGroupUsers(
                group=group_id,
                members_to_remove=[g.user_id for g in group_grants if not g.is_owner],
                owners_to_remove=[g.user_id for g in group_grants if g.is_owner],
            ).execute()
        except Exception:
            await db.session.rollback()
            logger.exception(f"Failed to prune redundant direct access in group {group_id}, skipping.")
            summary.decisions.extend(GrantDecision(g, PruneOutcome.FAILED) for g in group_grants)
            continue
        summary.decisions.extend(GrantDecision(g, PruneOutcome.REMOVED) for g in group_grants)

    summary.decisions.sort(key=_decision_sort_key)
    return summary


def _decision_sort_key(decision: GrantDecision) -> tuple[str, str, bool]:
    """Order decisions by group, then user, then dimension, so a run's report is
    reproducible regardless of the order groups were processed in."""
    return (decision.grant.group_name, decision.grant.user_email, decision.grant.is_owner)
