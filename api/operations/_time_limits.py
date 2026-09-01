"""Applying a time limit to access that groups and roles confer.

Every function here narrows `ended_at` and never extends it: each update is
guarded on the existing end date being absent or later than the limit. That
makes them idempotent and safe to compose in any order, which is what lets a
caller express a policy as a few calls instead of a wall of near-identical
bulk updates.

None of them commit. The caller owns the transaction.

Constraints apply only to managed, non-deleted entities, and the callers here
are handed ids they have already filtered; it is the caller that decides an
entity is subject to a constraint. Re-deriving that would add a join to every
bulk update and would quietly absorb a caller's mistake instead of letting it
show. **Callers must pass only managed, non-deleted ids.**

The invariant these maintain: a membership materialized from a role -- one
carrying a `role_group_map_id` -- must never outlive either of the two records
it derives from, the user's membership in the role and the role's association
with the group. `ModifyRoleGroups` and `ModifyGroupUsers` establish that when
they create such a row, by taking the minimum of the two. Anything that later
shortens one of those inputs has to re-establish it.
"""

from datetime import datetime
from typing import Collection, Optional

from sqlalchemy import func, or_, select, update

from api.extensions import db
from api.models import OktaGroupTagMap, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.models.tag import coalesce_constraints


async def limit_memberships_to_groups(group_ids: Collection[str], *, is_owner: bool, end_at: datetime) -> None:
    """Cap the access granted *to* `group_ids` on the `is_owner` side.

    Covers both ways access into a group is held: directly by a user, and by a
    role whose members inherit it. Passing role ids is meaningful and does the
    right thing -- a role is a group, and capping its inbound access caps
    membership of the role. The `RoleGroupMap` half of that is simply empty,
    since a role can never be another role's target.

    Args:
        group_ids: The groups whose inbound access is being capped. Must be
            managed and not deleted; see the module docstring.
        is_owner: Whether to cap ownership (True) or membership (False).
        end_at: The end date to cap at.
    """
    if len(group_ids) == 0:
        return

    await db.session.execute(
        update(OktaUserGroupMember)
        .where(OktaUserGroupMember.group_id.in_(group_ids))
        .where(OktaUserGroupMember.is_owner.is_(is_owner))
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > end_at,
            )
        )
        .values({OktaUserGroupMember.ended_at: end_at})
        .execution_options(synchronize_session="fetch")
    )

    await db.session.execute(
        update(RoleGroupMap)
        .where(RoleGroupMap.group_id.in_(group_ids))
        .where(RoleGroupMap.is_owner.is_(is_owner))
        .where(
            or_(
                RoleGroupMap.ended_at.is_(None),
                RoleGroupMap.ended_at > end_at,
            )
        )
        .values({RoleGroupMap.ended_at: end_at})
        .execution_options(synchronize_session="fetch")
    )


async def limit_memberships_by_roles(role_ids: Collection[str], *, is_owner: bool, end_at: datetime) -> None:
    """Cap the access granted *by* `role_ids` through their `is_owner`-side associations.

    These are the materialized rows in the groups each role is associated with.
    A role's member-side association produces member-side rows and its
    owner-side association owner-side rows, so `is_owner` selects both together.

    Args:
        role_ids: The roles whose conferred access is being capped. Must be
            managed and not deleted; see the module docstring.
        is_owner: Which association side to follow, and therefore which side of
            materialized row to cap.
        end_at: The end date to cap at.
    """
    if len(role_ids) == 0:
        return

    association_ids = (
        await db.session.scalars(
            select(RoleGroupMap.id)
            .where(RoleGroupMap.role_group_id.in_(role_ids))
            .where(RoleGroupMap.is_owner.is_(is_owner))
            .where(
                or_(
                    RoleGroupMap.ended_at.is_(None),
                    RoleGroupMap.ended_at > func.now(),
                )
            )
        )
    ).all()
    if len(association_ids) == 0:
        return

    await db.session.execute(
        update(OktaUserGroupMember)
        .where(OktaUserGroupMember.role_group_map_id.in_(association_ids))
        .where(OktaUserGroupMember.is_owner.is_(is_owner))
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > end_at,
            )
        )
        .values({OktaUserGroupMember.ended_at: end_at})
        .execution_options(synchronize_session="fetch")
    )


async def limit_access_conferred_by_roles(role_ids: Collection[str], *, end_at: datetime) -> None:
    """Cap membership of `role_ids` and everything that membership grants.

    A limit reaching a role always lands on its member side -- being a member
    of a role is what confers the role's access, so that is the only side there
    is anything to limit. Capping the memberships alone would leave the rows
    they produced in the associated groups on their old end dates, outliving
    the membership they exist because of, so both association sides are
    followed: a role's members hold membership through its member-side
    associations and ownership through its owner-side ones, and both derive
    from the same membership.

    Args:
        role_ids: The roles whose members are being capped. Must be managed and
            not deleted; see the module docstring.
        end_at: The end date to cap at.
    """
    if len(role_ids) == 0:
        return

    await limit_memberships_to_groups(role_ids, is_owner=False, end_at=end_at)
    await limit_memberships_by_roles(role_ids, is_owner=False, end_at=end_at)
    await limit_memberships_by_roles(role_ids, is_owner=True, end_at=end_at)


async def limit_roles_associated_with_groups(group_ids: Collection[str], *, is_owner: bool, end_at: datetime) -> None:
    """Cap the roles reached by a constraint on `group_ids`.

    `is_owner` selects the association axis, not the side the limit lands on:
    a role that is a member of these groups is reached by their member-side
    limit and a role that owns them by their owner-side limit, but either way
    the limit applies to the role's members. Unmanaged and deleted roles are
    skipped, matching `effective_ended_at` and the `cap-role-memberships`
    sweep.

    Args:
        group_ids: The groups the constraint sits on.
        is_owner: The association axis to follow.
        end_at: The end date to cap at.
    """
    if len(group_ids) == 0:
        return

    associated_role_ids = (
        await db.session.scalars(
            select(RoleGroupMap.role_group_id)
            .join(RoleGroup, RoleGroup.id == RoleGroupMap.role_group_id)
            .where(RoleGroupMap.group_id.in_(group_ids))
            .where(RoleGroupMap.is_owner.is_(is_owner))
            .where(RoleGroup.deleted_at.is_(None))
            .where(RoleGroup.is_managed.is_(True))
            .where(
                or_(
                    RoleGroupMap.ended_at.is_(None),
                    RoleGroupMap.ended_at > func.now(),
                )
            )
        )
    ).all()

    await limit_access_conferred_by_roles(associated_role_ids, end_at=end_at)


async def propagating_seconds_limit(group_ids: Collection[str], constraint_key: str) -> Optional[int]:
    """The limit `group_ids`' tags impose on a role associated with them.

    Reads the groups' active tags directly rather than through
    `effective_constraint`, because the caller has ids rather than a loaded
    object graph. Only enabled tags that propagate contribute.

    Args:
        group_ids: The groups the role is being associated with.
        constraint_key: `MEMBER_TIME_LIMIT_CONSTRAINT_KEY` for a member-side
            association, `OWNER_TIME_LIMIT_CONSTRAINT_KEY` for an owner-side one.

    Returns:
        The coalesced limit in seconds, or None if no propagating tag sets it.
    """
    if len(group_ids) == 0:
        return None

    tags = (
        await db.session.scalars(
            select(Tag)
            .join(OktaGroupTagMap, OktaGroupTagMap.tag_id == Tag.id)
            .where(OktaGroupTagMap.group_id.in_(group_ids))
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
            .where(Tag.deleted_at.is_(None))
            .where(Tag.propagate_to_roles.is_(True))
        )
    ).all()

    # `coalesce_constraints` applies the `enabled` filter and the constraint's
    # own coalesce rule, so the minimum here comes from the same code that
    # resolves it everywhere else.
    return coalesce_constraints(constraint_key, list(tags))
