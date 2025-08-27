from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api_v2.models import OktaGroup, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api_v2.models.tag import coalesce_constraints


class ModifyGroupsTimeLimit:
    def __init__(self, db: Session, *, groups: list[str] | set[str], tags: list[str] | set[str]):
        self.db = db
        
        # Only include groups that are managed
        self.groups = (
            self.db.query(OktaGroup).filter(OktaGroup.id.in_(groups))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.is_managed.is_(True))
            .all()
        )
        self.role_groups = (
            self.db.query(RoleGroup).filter(RoleGroup.id.in_(groups))
            .filter(RoleGroup.deleted_at.is_(None))
            .filter(RoleGroup.is_managed.is_(True))
            .all()
        )

        self.tags = self.db.query(Tag).filter(Tag.id.in_(tags)).filter(Tag.deleted_at.is_(None)).all()

    def execute(self) -> None:
        if len(self.groups) == 0:
            return

        # Determine the minimum time allowed for group membership and ownership by current group tags
        membership_seconds_limit = coalesce_constraints(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, self.tags)
        ownership_seconds_limit = coalesce_constraints(Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, self.tags)
        # Handle group time limit constraints when adding tags with time limit contraints to a group
        if membership_seconds_limit is not None:
            membership_time_limit_from_now = datetime.now(UTC) + timedelta(seconds=membership_seconds_limit)
            # Reduce all user memberships for the given groups to minimum allowed time limit
            self.db.query(OktaUserGroupMember).filter(OktaUserGroupMember.group_id.in_([g.id for g in self.groups])).filter(
                OktaUserGroupMember.is_owner.is_(False)
            ).filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > membership_time_limit_from_now,
                )
            ).update(
                {OktaUserGroupMember.ended_at: membership_time_limit_from_now},
                synchronize_session="fetch",
            )
            # Reduce all role memberships for the given groups to the minimum allowed time limit
            self.db.query(RoleGroupMap).filter(RoleGroupMap.group_id.in_([g.id for g in self.groups])).filter(
                RoleGroupMap.is_owner.is_(False)
            ).filter(
                or_(
                    RoleGroupMap.ended_at.is_(None),
                    RoleGroupMap.ended_at > membership_time_limit_from_now,
                )
            ).update(
                {RoleGroupMap.ended_at: membership_time_limit_from_now},
                synchronize_session="fetch",
            )
            # Reduce all user memberships for groups associated with any given role groups
            # to the minimum allowed time limit
            role_group_map_associations = (
                self.db.query(RoleGroupMap).filter(RoleGroupMap.role_group_id.in_([g.id for g in self.role_groups]))
                .filter(RoleGroupMap.is_owner.is_(False))
                .filter(
                    or_(
                        RoleGroupMap.ended_at.is_(None),
                        RoleGroupMap.ended_at > func.now(),
                    )
                )
                .all()
            )
            self.db.query(OktaUserGroupMember).filter(
                OktaUserGroupMember.role_group_map_id.in_([m.id for m in role_group_map_associations])
            ).filter(OktaUserGroupMember.is_owner.is_(False)).filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > membership_time_limit_from_now,
                )
            ).update(
                {OktaUserGroupMember.ended_at: membership_time_limit_from_now},
                synchronize_session="fetch",
            )
            self.db.commit()
        if ownership_seconds_limit is not None:
            ownership_time_limit_from_now = datetime.now(UTC) + timedelta(seconds=ownership_seconds_limit)
            # Reduce all user ownerships for the given groups to minimum allowed time limit
            self.db.query(OktaUserGroupMember).filter(OktaUserGroupMember.group_id.in_([g.id for g in self.groups])).filter(
                OktaUserGroupMember.is_owner.is_(True)
            ).filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > ownership_time_limit_from_now,
                )
            ).update(
                {OktaUserGroupMember.ended_at: ownership_time_limit_from_now},
                synchronize_session="fetch",
            )
            # Reduce all role ownerships for the given groups to the minimum allowed time limit
            self.db.query(RoleGroupMap).filter(RoleGroupMap.group_id.in_([g.id for g in self.groups])).filter(
                RoleGroupMap.is_owner.is_(True)
            ).filter(
                or_(
                    RoleGroupMap.ended_at.is_(None),
                    RoleGroupMap.ended_at > ownership_time_limit_from_now,
                )
            ).update(
                {RoleGroupMap.ended_at: ownership_time_limit_from_now},
                synchronize_session="fetch",
            )
            # Reduce all user ownerships for groups associated with any given role groups
            # to the minimum allowed time limit
            role_group_map_associations = (
                self.db.query(RoleGroupMap).filter(RoleGroupMap.role_group_id.in_([g.id for g in self.role_groups]))
                .filter(RoleGroupMap.is_owner.is_(True))
                .filter(
                    or_(
                        RoleGroupMap.ended_at.is_(None),
                        RoleGroupMap.ended_at > func.now(),
                    )
                )
                .all()
            )
            self.db.query(OktaUserGroupMember).filter(
                OktaUserGroupMember.role_group_map_id.in_([m.id for m in role_group_map_associations])
            ).filter(OktaUserGroupMember.is_owner.is_(True)).filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > membership_time_limit_from_now,
                )
            ).update(
                {OktaUserGroupMember.ended_at: membership_time_limit_from_now},
                synchronize_session="fetch",
            )
            self.db.commit()