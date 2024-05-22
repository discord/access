from typing import Optional, Tuple

from sqlalchemy.orm import (
    selectin_polymorphic,
    selectinload,
)

from api.authorization import AuthorizationHelpers
from api.extensions import db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.models.tag import coalesce_constraints


class CheckForSelfAdd:
    def __init__(
        self,
        group: OktaGroup | str,
        current_user: Optional[OktaUser | str],
        members_to_add: list[str] = [],
        owners_to_add: list[str] = [],
    ):
        self.group = (
            db.session.query(OktaGroup)
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

        if current_user is None:
            self.current_user = None
        else:
            self.current_user = (
                OktaUser.query.filter(OktaUser.deleted_at.is_(None))
                .filter(OktaUser.id == (current_user if isinstance(current_user, str) else current_user.id))
                .first()
            )

        self.members_to_add = members_to_add
        self.owners_to_add = owners_to_add

    def execute_for_group(self) -> Tuple[bool, str]:
        if self.current_user is None or AuthorizationHelpers.is_access_admin(self.current_user.id):
            return True, ""

        if len(self.owners_to_add) > 0 and self.current_user.id in self.owners_to_add:
            disallow_self_add_ownership = coalesce_constraints(
                constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                tags=[tag_map.active_tag for tag_map in self.group.active_group_tags],
            )
            if self.group.is_managed and disallow_self_add_ownership is True:
                return (
                    False,
                    "Current user is an group owner who is restricted "
                    + f"from readding themself as owner to {self.group.name} due to group tags",
                )
        if len(self.members_to_add) > 0 and self.current_user.id in self.members_to_add:
            disallow_self_add_membership = coalesce_constraints(
                constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                tags=[tag_map.active_tag for tag_map in self.group.active_group_tags],
            )
            if self.group.is_managed and disallow_self_add_membership is True:
                return (
                    False,
                    "Current user is a group owner who is restricted "
                    + f"from adding themself as member to {self.group.name} due to group tags",
                )

            # If the group is a role group check to see if a reason is required for adding members or owners
            # to the associated groups
            if type(self.group) == RoleGroup and self.group.is_managed:
                member_groups = [rm.active_group for rm in self.group.active_role_associated_group_member_mappings]
                for member_group in member_groups:
                    disallow_self_add_membership = coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in member_group.active_group_tags],
                    )
                    if member_group.is_managed and disallow_self_add_membership is True:
                        return (
                            False,
                            "Current user is a role owner who is restricted from adding themself as "
                            + f"member to {self.group.name} because the associated group {member_group.name} "
                            + "has group tags which restricts self-adding membership",
                        )
                owner_groups = [rm.active_group for rm in self.group.active_role_associated_group_owner_mappings]
                for owner_group in owner_groups:
                    disallow_self_add_ownership = coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                    )
                    if owner_group.is_managed and disallow_self_add_ownership is True:
                        return (
                            False,
                            "Current user is a role owner who is restricted from adding themself as "
                            + f"member to {self.group.name} because the associated group {member_group.name} "
                            + "has group tags which restricts self-adding ownership",
                        )
        return True, ""

    def execute_for_role(self) -> Tuple[bool, str]:
        if self.current_user is None or AuthorizationHelpers.is_access_admin(self.current_user.id):
            return True, ""

        if type(self.group) != RoleGroup:
            return True, ""

        # Check to see if the current user is a member of the role,
        # which would grant them access to the newly added groups associated with the role
        if (
            OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == self.group.id)
            .filter(OktaUserGroupMember.user_id == self.current_user.id)
            .filter(OktaUserGroupMember.is_owner.is_(False))
            .filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .count()
            > 0
        ):
            if len(self.members_to_add) > 0:
                new_member_groups = (
                    OktaGroup.query.options(
                        selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag)
                    )
                    .filter(OktaGroup.is_managed.is_(True))
                    .filter(OktaGroup.id.in_(self.members_to_add))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .all()
                )
                for member_group in new_member_groups:
                    require_member_reason = coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in member_group.active_group_tags],
                    )
                    if require_member_reason is True:
                        return (
                            False,
                            "Current user is a role member who is restricted from adding "
                            + f"{self.group.name} as a member to {member_group.name} because that group  "
                            + "has tags which restricts self-adding membership",
                        )

            if len(self.owners_to_add) > 0:
                new_owner_groups = (
                    OktaGroup.query.options(
                        selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag)
                    )
                    .filter(OktaGroup.is_managed.is_(True))
                    .filter(OktaGroup.id.in_(self.owners_to_add))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .all()
                )
                for owner_group in new_owner_groups:
                    require_owner_reason = coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                    )
                    if require_owner_reason is True:
                        return (
                            False,
                            "Current user is a role member who is restricted from adding "
                            + f"{self.group.name} as an owner to {owner_group.name} because that group  "
                            + "has tags which restricts self-adding ownership",
                        )
        return True, ""
