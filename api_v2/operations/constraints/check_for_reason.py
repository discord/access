"""
Check for reason constraint for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

from typing import Optional, Tuple

from sqlalchemy.orm import Session, selectin_polymorphic, selectinload

from api_v2.models import AppGroup, OktaGroup, OktaGroupTagMap, RoleGroup, RoleGroupMap, Tag
from api_v2.models.tag import coalesce_constraints


class CheckForReason:
    def __init__(
        self,
        db: Session,
        group: OktaGroup | str,
        reason: Optional[str],
        members_to_add: list[str] = [],
        owners_to_add: list[str] = [],
    ):
        self.db = db
        self.group = (
            self.db.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                selectinload(RoleGroup.active_role_associated_group_member_mappings)
                .joinedload(RoleGroupMap.active_group)
                .selectinload(OktaGroup.active_group_tags)
                .joinedload(OktaGroupTagMap.active_tag),
                selectinload(RoleGroup.active_role_associated_group_owner_mappings)
                .joinedload(RoleGroupMap.active_group)
                .selectinload(OktaGroup.active_group_tags)
                .joinedload(OktaGroupTagMap.active_tag),
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
            .first()
        )

        self.reason = reason
        self.members_to_add = members_to_add
        self.owners_to_add = owners_to_add

    @staticmethod
    def invalid_reason(reason: Optional[str]) -> bool:
        return reason is None or reason.strip() == ""

    def execute_for_group(self) -> Tuple[bool, str]:
        if self.invalid_reason(self.reason):
            tags = [tag_map.active_tag for tag_map in self.group.active_group_tags]
            if len(self.owners_to_add) > 0:
                require_owner_reason = coalesce_constraints(
                    constraint_key=Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY, tags=tags
                )
                if self.group.is_managed and require_owner_reason is True:
                    return False, f"Reason for adding owners to {self.group.name} group is required due to group tags"
            if len(self.members_to_add) > 0:
                require_member_reason = coalesce_constraints(
                    constraint_key=Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY, tags=tags
                )
                if self.group.is_managed and require_member_reason is True:
                    return False, f"Reason for adding members to {self.group.name} group is required due to group tags"

                # If the group is a role group check to see if a reason is required for adding members or owners
                # to the associated groups
                if type(self.group) is RoleGroup and self.group.is_managed:
                    member_groups = [rm.active_group for rm in self.group.active_role_associated_group_member_mappings]
                    for member_group in member_groups:
                        require_member_reason = coalesce_constraints(
                            constraint_key=Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY,
                            tags=[tag_map.active_tag for tag_map in member_group.active_group_tags],
                        )
                        if member_group.is_managed and require_member_reason is True:
                            return (
                                False,
                                f"Reason for adding members to {member_group.name} group associated "
                                + f"with role {self.group.name} is required due to group tags",
                            )
                    owner_groups = [rm.active_group for rm in self.group.active_role_associated_group_owner_mappings]
                    for owner_group in owner_groups:
                        require_owner_reason = coalesce_constraints(
                            constraint_key=Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY,
                            tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                        )
                        if owner_group.is_managed and require_owner_reason is True:
                            return (
                                False,
                                f"Reason for adding owners to {owner_group.name} group associated "
                                + f"with role {self.group.name} is required due to group tags",
                            )
        return True, ""

    def execute_for_role(self) -> Tuple[bool, str]:
        if type(self.group) is not RoleGroup:
            return True, ""

        if self.invalid_reason(self.reason):
            if len(self.members_to_add) > 0:
                new_member_groups = (
                    self.db.query(OktaGroup)
                    .options(selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag))
                    .filter(OktaGroup.is_managed.is_(True))
                    .filter(OktaGroup.id.in_(self.members_to_add))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .all()
                )
                for member_group in new_member_groups:
                    require_member_reason = coalesce_constraints(
                        constraint_key=Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in member_group.active_group_tags],
                    )
                    if require_member_reason is True:
                        return (
                            False,
                            f"Reason for adding role {self.group.name} as members "
                            + f"to {member_group.name} group is required due to group tags",
                        )

            if len(self.owners_to_add) > 0:
                new_owner_groups = (
                    self.db.query(OktaGroup)
                    .options(selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag))
                    .filter(OktaGroup.is_managed.is_(True))
                    .filter(OktaGroup.id.in_(self.owners_to_add))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .all()
                )
                for owner_group in new_owner_groups:
                    require_owner_reason = coalesce_constraints(
                        constraint_key=Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                    )
                    if require_owner_reason is True:
                        return (
                            False,
                            f"Reason for adding role {self.group.name} as owners "
                            + f"to {owner_group.name} group is required due to group tags",
                        )
        return True, ""