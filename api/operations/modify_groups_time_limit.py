from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from api.extensions import db
from api.models import OktaGroup, RoleGroup, Tag
from api.models.tag import coalesce_constraints
from api.operations._time_limits import (
    limit_memberships_by_roles,
    limit_memberships_to_groups,
    limit_roles_associated_with_groups,
)


class ModifyGroupsTimeLimit:
    """Apply the time limits a set of tags imposes on a set of groups.

    Runs when a tag's constraints start applying to groups that already have
    access granted on them -- the tag being attached, edited, or inherited from
    an app. Existing grants are shortened to fit; nothing is ever extended.

    A tag reaches access three ways, and each is a separate call below:

    1. Directly, to the tagged groups' own memberships and ownerships.
    2. Through a tagged *role*, to the access that role confers on the groups
       it is associated with.
    3. By propagation, to roles associated with a tagged group -- their members
       are governed by the tagged group's limit, and so is everything those
       memberships in turn confer.
    """

    def __init__(self, groups: list[str] | set[str], tags: list[str] | set[str]):
        self.group_ids = groups
        self.tag_ids = tags

    async def execute(self) -> None:
        # Only managed groups are subject to constraints.
        groups = (
            await db.session.scalars(
                select(OktaGroup)
                .where(OktaGroup.id.in_(self.group_ids))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.is_managed.is_(True))
            )
        ).all()
        if len(groups) == 0:
            return

        role_groups = (
            await db.session.scalars(
                select(RoleGroup)
                .where(RoleGroup.id.in_(self.group_ids))
                .where(RoleGroup.deleted_at.is_(None))
                .where(RoleGroup.is_managed.is_(True))
            )
        ).all()
        tags = list(
            (
                await db.session.scalars(select(Tag).where(Tag.id.in_(self.tag_ids)).where(Tag.deleted_at.is_(None)))
            ).all()
        )

        group_ids = [g.id for g in groups]
        role_group_ids = [g.id for g in role_groups]
        # Only tags that propagate reach an associated role.
        propagating_tags = [t for t in tags if t.propagate_to_roles]

        # One pass per side, each carrying its own limit. A tag may set either
        # limit without the other, so neither pass can borrow the other's
        # value or depend on the other having run.
        for is_owner, constraint_key in (
            (False, Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY),
            (True, Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY),
        ):
            seconds_limit = coalesce_constraints(constraint_key, tags)
            if seconds_limit is not None:
                end_at = datetime.now(UTC) + timedelta(seconds=seconds_limit)
                await limit_memberships_to_groups(group_ids, is_owner=is_owner, end_at=end_at)
                await limit_memberships_by_roles(role_group_ids, is_owner=is_owner, end_at=end_at)

            # A role that is a MEMBER of these groups is governed by their
            # member limit and a role that OWNS them by their owner limit;
            # either way the limit lands on the role's members. Computed from
            # the propagating tags only, and independent of whether the group's
            # own limit above applied.
            propagated_seconds_limit = coalesce_constraints(constraint_key, propagating_tags)
            if propagated_seconds_limit is not None:
                await limit_roles_associated_with_groups(
                    group_ids,
                    is_owner=is_owner,
                    end_at=datetime.now(UTC) + timedelta(seconds=propagated_seconds_limit),
                )

        await db.session.commit()
