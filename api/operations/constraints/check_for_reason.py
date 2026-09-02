from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import (
    selectin_polymorphic,
    selectinload,
)

from api.extensions import db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, RoleGroup, Tag
from api.models.tag import coalesce_constraints, constraint_source_clause, effective_constraint
from api.routers._eager import effective_constraint_options


class CheckForReason:
    def __init__(
        self,
        group: OktaGroup | str,
        reason: Optional[str],
        members_to_add: list[str] = [],
        owners_to_add: list[str] = [],
    ):
        # `members_to_add` / `owners_to_add` are id lists: OktaUser ids for the
        # direct group path (execute_for_group), or the group ids a role is
        # added to for the role path (execute_for_role).
        self.group_id = group if isinstance(group, str) else group.id

        self.reason = reason

        self.members_to_add = members_to_add
        self.owners_to_add = owners_to_add

    @staticmethod
    def invalid_reason(reason: Optional[str]) -> bool:
        return reason is None or reason.strip() == ""

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

        if self.invalid_reason(self.reason):
            if len(self.owners_to_add) > 0:
                key = Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY
                if group.is_managed and effective_constraint(key, group) is True:
                    clause = constraint_source_clause(key, group)
                    return False, f"Reason for adding owners to {group.name} group is required {clause}"
            if len(self.members_to_add) > 0:
                key = Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY
                if group.is_managed and effective_constraint(key, group) is True:
                    clause = constraint_source_clause(key, group)
                    return False, f"Reason for adding members to {group.name} group is required {clause}"
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

        if type(group) is not RoleGroup:
            return True, ""

        if self.invalid_reason(self.reason):
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
                        constraint_key=Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in member_group.active_group_tags],
                    )
                    if require_member_reason is True:
                        return (
                            False,
                            f"Reason for adding role {group.name} as members "
                            + f"to {member_group.name} group is required due to group tags",
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
                        constraint_key=Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY,
                        tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                    )
                    if require_owner_reason is True:
                        return (
                            False,
                            f"Reason for adding role {group.name} as owners "
                            + f"to {owner_group.name} group is required due to group tags",
                        )
        return True, ""
