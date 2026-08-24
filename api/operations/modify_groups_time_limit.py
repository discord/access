from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from api.extensions import db
from api.models import OktaGroup, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.models.tag import coalesce_constraints


class ModifyGroupsTimeLimit:
    def __init__(self, groups: list[str] | set[str], tags: list[str] | set[str]):
        self.group_ids = groups
        self.tag_ids = tags

    async def execute(self) -> None:
        # Only include groups that are managed
        groups = (
            await db.session.scalars(
                select(OktaGroup)
                .where(OktaGroup.id.in_(self.group_ids))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.is_managed.is_(True))
            )
        ).all()
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

        if len(groups) == 0:
            return

        # Only tags that propagate contribute a limit to associated roles.
        propagating_tags = [t for t in tags if t.propagate_to_roles]
        # A role that is a MEMBER of these groups is capped by their member
        # limit; a role that OWNS them is capped by their owner limit. Both
        # land on the role's own user memberships.
        role_member_limit = coalesce_constraints(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, propagating_tags)
        role_owner_limit = coalesce_constraints(Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, propagating_tags)

        # Determine the minimum time allowed for group membership and ownership by current group tags
        membership_seconds_limit = coalesce_constraints(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, tags)
        ownership_seconds_limit = coalesce_constraints(Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, tags)
        # Handle group time limit constraints when adding tags with time limit contraints to a group
        if membership_seconds_limit is not None:
            membership_time_limit_from_now = datetime.now(UTC) + timedelta(seconds=membership_seconds_limit)
            # Reduce all user memberships for the given groups to minimum allowed time limit
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.group_id.in_([g.id for g in groups]))
                .where(OktaUserGroupMember.is_owner.is_(False))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > membership_time_limit_from_now,
                    )
                )
                .values({OktaUserGroupMember.ended_at: membership_time_limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
            # Reduce all role memberships for the given groups to the minimum allowed time limit
            await db.session.execute(
                update(RoleGroupMap)
                .where(RoleGroupMap.group_id.in_([g.id for g in groups]))
                .where(RoleGroupMap.is_owner.is_(False))
                .where(
                    or_(
                        RoleGroupMap.ended_at.is_(None),
                        RoleGroupMap.ended_at > membership_time_limit_from_now,
                    )
                )
                .values({RoleGroupMap.ended_at: membership_time_limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
            # Reduce all user memberships for groups associated with any given role groups
            # to the minimum allowed time limit
            role_group_map_associations = (
                await db.session.scalars(
                    select(RoleGroupMap)
                    .where(RoleGroupMap.role_group_id.in_([g.id for g in role_groups]))
                    .where(RoleGroupMap.is_owner.is_(False))
                    .where(
                        or_(
                            RoleGroupMap.ended_at.is_(None),
                            RoleGroupMap.ended_at > func.now(),
                        )
                    )
                )
            ).all()
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.role_group_map_id.in_([m.id for m in role_group_map_associations]))
                .where(OktaUserGroupMember.is_owner.is_(False))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > membership_time_limit_from_now,
                    )
                )
                .values({OktaUserGroupMember.ended_at: membership_time_limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
            await db.session.commit()
        if ownership_seconds_limit is not None:
            ownership_time_limit_from_now = datetime.now(UTC) + timedelta(seconds=ownership_seconds_limit)
            # Reduce all user ownerships for the given groups to minimum allowed time limit
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.group_id.in_([g.id for g in groups]))
                .where(OktaUserGroupMember.is_owner.is_(True))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > ownership_time_limit_from_now,
                    )
                )
                .values({OktaUserGroupMember.ended_at: ownership_time_limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
            # Reduce all role ownerships for the given groups to the minimum allowed time limit
            await db.session.execute(
                update(RoleGroupMap)
                .where(RoleGroupMap.group_id.in_([g.id for g in groups]))
                .where(RoleGroupMap.is_owner.is_(True))
                .where(
                    or_(
                        RoleGroupMap.ended_at.is_(None),
                        RoleGroupMap.ended_at > ownership_time_limit_from_now,
                    )
                )
                .values({RoleGroupMap.ended_at: ownership_time_limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
            # Reduce all user ownerships for groups associated with any given role groups
            # to the minimum allowed time limit
            role_group_map_associations = (
                await db.session.scalars(
                    select(RoleGroupMap)
                    .where(RoleGroupMap.role_group_id.in_([g.id for g in role_groups]))
                    .where(RoleGroupMap.is_owner.is_(True))
                    .where(
                        or_(
                            RoleGroupMap.ended_at.is_(None),
                            RoleGroupMap.ended_at > func.now(),
                        )
                    )
                )
            ).all()
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.role_group_map_id.in_([m.id for m in role_group_map_associations]))
                .where(OktaUserGroupMember.is_owner.is_(True))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > membership_time_limit_from_now,
                    )
                )
                .values({OktaUserGroupMember.ended_at: membership_time_limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
            await db.session.commit()

        # Retroactive capping: a time-limited tag landing on a group must also
        # reach the existing user memberships of any role already associated
        # with that group -- not just future grants. This is independent of
        # whether the group's own membership/ownership limits (above) applied.
        # Compute the full set of affected roles once per direction and issue
        # a single bulk update per direction; this operation commits, so
        # per-role updates would commit once per role.
        group_ids = [g.id for g in groups]
        for is_owner_association, seconds_limit in (
            (False, role_member_limit),
            (True, role_owner_limit),
        ):
            if seconds_limit is None:
                continue
            limit_from_now = datetime.now(UTC) + timedelta(seconds=seconds_limit)
            # Unmanaged roles are exempt: `effective_ended_at` returns early
            # for a group that is not managed, and the `cap-role-memberships`
            # sweep filters `RoleGroup.is_managed`. Join through so this third
            # enforcement point agrees with the other two.
            associated_role_ids = (
                await db.session.scalars(
                    select(RoleGroupMap.role_group_id)
                    .join(RoleGroup, RoleGroup.id == RoleGroupMap.role_group_id)
                    .where(RoleGroupMap.group_id.in_(group_ids))
                    .where(RoleGroupMap.is_owner.is_(is_owner_association))
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
            if len(associated_role_ids) == 0:
                continue
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.group_id.in_(associated_role_ids))
                .where(OktaUserGroupMember.is_owner.is_(False))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > limit_from_now,
                    )
                )
                .values({OktaUserGroupMember.ended_at: limit_from_now})
                .execution_options(synchronize_session="fetch")
            )
        await db.session.commit()
