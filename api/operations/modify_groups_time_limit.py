from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from api.extensions import db
from api.models import OktaGroup, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.models.tag import coalesce_constraints


class ModifyGroupsTimeLimit:
    def __init__(self, groups: list[str] | set[str], tags: list[str] | set[str]):
        self._groups_arg = groups
        self._tags_arg = tags

    async def _resolve(self) -> None:
        groups = self._groups_arg
        # Only include groups that are managed
        self.groups = (
            await db.session.scalars(
                select(OktaGroup)
                .where(OktaGroup.id.in_(groups))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.is_managed.is_(True))
            )
        ).all()
        self.role_groups = (
            await db.session.scalars(
                select(RoleGroup)
                .where(RoleGroup.id.in_(groups))
                .where(RoleGroup.deleted_at.is_(None))
                .where(RoleGroup.is_managed.is_(True))
            )
        ).all()

        self.tags = (
            await db.session.scalars(select(Tag).where(Tag.id.in_(self._tags_arg)).where(Tag.deleted_at.is_(None)))
        ).all()

    async def execute(self) -> None:
        await self._resolve()
        if len(self.groups) == 0:
            return

        # Determine the minimum time allowed for group membership and ownership by current group tags
        membership_seconds_limit = coalesce_constraints(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, self.tags)
        ownership_seconds_limit = coalesce_constraints(Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, self.tags)
        # Handle group time limit constraints when adding tags with time limit contraints to a group
        if membership_seconds_limit is not None:
            membership_time_limit_from_now = datetime.now(UTC) + timedelta(seconds=membership_seconds_limit)
            # Reduce all user memberships for the given groups to minimum allowed time limit
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.group_id.in_([g.id for g in self.groups]))
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
                .where(RoleGroupMap.group_id.in_([g.id for g in self.groups]))
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
                    .where(RoleGroupMap.role_group_id.in_([g.id for g in self.role_groups]))
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
                .where(OktaUserGroupMember.group_id.in_([g.id for g in self.groups]))
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
                .where(RoleGroupMap.group_id.in_([g.id for g in self.groups]))
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
                    .where(RoleGroupMap.role_group_id.in_([g.id for g in self.role_groups]))
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
