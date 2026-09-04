from typing import Optional, Tuple

from sqlalchemy.orm import (
    selectin_polymorphic,
    selectinload,
)

from sqlalchemy import func, or_, select
from api.auth.permissions import is_access_admin as _is_access_admin
from api.extensions import db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, OktaUserGroupMember, RoleGroup, Tag
from api.models.tag import coalesce_constraints, constraint_source_clause, effective_constraint
from api.routers._eager import effective_constraint_options


class CheckForSelfAdd:
    def __init__(
        self,
        group: OktaGroup | str,
        current_user: Optional[OktaUser | str],
        members_to_add: list[str] = [],
        owners_to_add: list[str] = [],
    ):
        # `members_to_add` / `owners_to_add` are id lists: OktaUser ids for the
        # direct group path (execute_for_group), or the group ids a role is
        # added to for the role path (execute_for_role).
        self.group_id = group if isinstance(group, str) else group.id
        self.current_user_id = (
            current_user.id if current_user is not None and not isinstance(current_user, str) else current_user
        )

        self.members_to_add = members_to_add
        self.owners_to_add = owners_to_add

    async def execute_for_group(self) -> Tuple[bool, str]:
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    # The subtype loader stays here rather than in the helper: it is
                    # what makes the `RoleGroup` paths resolvable on a polymorphic
                    # `OktaGroup` query, and a query selecting `RoleGroup` directly
                    # needs no such pairing.
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    *effective_constraint_options(),
                )
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.group_id)
            )
        ).first()
        assert group is not None

        if self.current_user_id is None:
            current_user = None
        else:
            current_user = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first()

        if current_user is None or await _is_access_admin(db.session, current_user.id):
            return True, ""

        if len(self.owners_to_add) > 0 and current_user.id in self.owners_to_add:
            key = Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY
            if group.is_managed and effective_constraint(key, group) is True:
                clause = constraint_source_clause(key, group)
                return (
                    False,
                    "Current user is a group owner who is restricted "
                    + f"from re-adding themself as owner to {group.name} {clause}",
                )
        if len(self.members_to_add) > 0 and current_user.id in self.members_to_add:
            # For a role, `effective_constraint` also covers the groups the
            # role is a member of (same key) and the groups it owns (owner-side
            # key). The clause names which group imposed the restriction.
            key = Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY
            if group.is_managed and effective_constraint(key, group) is True:
                clause = constraint_source_clause(key, group)
                return (
                    False,
                    "Current user is a group owner who is restricted "
                    + f"from adding themself as member to {group.name} {clause}",
                )
        return True, ""

    async def execute_for_role(self) -> Tuple[bool, str]:
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    # The subtype loader stays here rather than in the helper: it is
                    # what makes the `RoleGroup` paths resolvable on a polymorphic
                    # `OktaGroup` query, and a query selecting `RoleGroup` directly
                    # needs no such pairing.
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    *effective_constraint_options(),
                )
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.group_id)
            )
        ).first()

        if self.current_user_id is None:
            current_user = None
        else:
            current_user = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first()

        if current_user is None or await _is_access_admin(db.session, current_user.id):
            return True, ""

        if type(group) is not RoleGroup:
            return True, ""

        # Check to see if the current user is a member of the role,
        # which would grant them access to the newly added groups associated with the role
        if (
            await db.session.scalar(
                select(func.count()).select_from(
                    select(OktaUserGroupMember)
                    .where(OktaUserGroupMember.group_id == group.id)
                    .where(OktaUserGroupMember.user_id == current_user.id)
                    .where(OktaUserGroupMember.is_owner.is_(False))
                    .where(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        )
                    )
                    .subquery()
                )
            )
            or 0
        ) > 0:
            if len(self.members_to_add) > 0:
                new_member_groups = (
                    await db.session.scalars(
                        select(OktaGroup)
                        .options(selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag))
                        .where(OktaGroup.is_managed.is_(True))
                        .where(OktaGroup.id.in_(self.members_to_add))
                        .where(OktaGroup.deleted_at.is_(None))
                    )
                ).all()
                for member_group in new_member_groups:
                    require_member_reason = coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in member_group.active_group_tags],
                    )
                    if require_member_reason is True:
                        return (
                            False,
                            "Current user is a role member who is restricted from adding "
                            + f"{group.name} as a member to {member_group.name} because that group "
                            + "has tags which restricts self-adding membership",
                        )

            if len(self.owners_to_add) > 0:
                new_owner_groups = (
                    await db.session.scalars(
                        select(OktaGroup)
                        .options(selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag))
                        .where(OktaGroup.is_managed.is_(True))
                        .where(OktaGroup.id.in_(self.owners_to_add))
                        .where(OktaGroup.deleted_at.is_(None))
                    )
                ).all()
                for owner_group in new_owner_groups:
                    require_owner_reason = coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                    )
                    if require_owner_reason is True:
                        return (
                            False,
                            "Current user is a role member who is restricted from adding "
                            + f"{group.name} as an owner to {owner_group.name} because that group "
                            + "has tags which restricts self-adding ownership",
                        )
        return True, ""
