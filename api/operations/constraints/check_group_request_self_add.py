from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from api.auth.permissions import is_access_admin
from api.extensions import db
from api.models import AppTagMap, OktaUserGroupMember, RoleGroup, Tag
from api.models.tag import coalesce_constraints


@dataclass
class GroupRequestSelfAdd:
    """The grants in a group request that would add its approver to the new group
    against a `disallow_self_add_*` constraint.

    Attributes:
        owner_user: The approver is a named owner.
        member_user: The approver is a named member.
        owner_role_ids: Named owner roles the approver is a member of.
        member_role_ids: Named member roles the approver is a member of.
        message: Explains the first blocked grant, or "" when nothing is blocked.
    """

    owner_user: bool = False
    member_user: bool = False
    owner_role_ids: list[str] = field(default_factory=list)
    member_role_ids: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def blocked(self) -> bool:
        """Whether any grant is blocked."""
        return self.owner_user or self.member_user or bool(self.owner_role_ids) or bool(self.member_role_ids)


class CheckGroupRequestSelfAdd:
    """Check whether approving a group request would let its approver add themself
    to the new group, directly or through a role, where the group's tags forbid it.

    The group does not exist yet, so the constraints are read from the tags it
    will carry: the request's tags plus, for an app group, its app's tags. Access
    admins and plugin approvals (no approver) are exempt, as for a direct add.

    Args:
        approver_id: The approving user, or None for a plugin approval.
        tag_ids: Tags the new group will carry directly.
        app_id: The new group's app, for an app group.
        owner_user_ids: Users to be made owners.
        member_user_ids: Users to be made members.
        owner_role_ids: Roles to be made owners.
        member_role_ids: Roles to be made members.
    """

    def __init__(
        self,
        *,
        approver_id: str | None,
        tag_ids: list[str],
        app_id: str | None,
        owner_user_ids: list[str],
        member_user_ids: list[str],
        owner_role_ids: list[str],
        member_role_ids: list[str],
    ):
        self.approver_id = approver_id
        self.tag_ids = tag_ids
        self.app_id = app_id
        self.owner_user_ids = owner_user_ids
        self.member_user_ids = member_user_ids
        self.owner_role_ids = owner_role_ids
        self.member_role_ids = member_role_ids

    async def execute(self) -> GroupRequestSelfAdd:
        """Return the blocked grants."""
        result = GroupRequestSelfAdd()
        if self.approver_id is None or await is_access_admin(db.session, self.approver_id):
            return result

        tags = await self._tags()
        disallow_owner = coalesce_constraints(Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY, tags) is True
        disallow_member = coalesce_constraints(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, tags) is True
        if not disallow_owner and not disallow_member:
            return result

        approver_roles = await self._approver_role_ids(set(self.owner_role_ids) | set(self.member_role_ids))
        if disallow_owner:
            result.owner_user = self.approver_id in self.owner_user_ids
            result.owner_role_ids = [r for r in self.owner_role_ids if r in approver_roles]
        if disallow_member:
            result.member_user = self.approver_id in self.member_user_ids
            result.member_role_ids = [r for r in self.member_role_ids if r in approver_roles]

        if result.owner_user or result.owner_role_ids:
            result.message = (
                "The approver is restricted from adding themself as an owner of this group, directly or through a "
                "role they are a member of, because its tags restrict self-adding ownership"
            )
        elif result.member_user or result.member_role_ids:
            result.message = (
                "The approver is restricted from adding themself as a member of this group, directly or through a "
                "role they are a member of, because its tags restrict self-adding membership"
            )
        return result

    async def _tags(self) -> list[Tag]:
        tags: list[Tag] = []
        if self.tag_ids:
            tags.extend(
                (
                    await db.session.scalars(
                        select(Tag).where(Tag.id.in_(self.tag_ids)).where(Tag.deleted_at.is_(None))
                    )
                ).all()
            )
        if self.app_id is not None:
            app_tag_maps = (
                await db.session.scalars(
                    select(AppTagMap)
                    .options(joinedload(AppTagMap.active_tag))
                    .where(AppTagMap.app_id == self.app_id)
                    .where(or_(AppTagMap.ended_at.is_(None), AppTagMap.ended_at > func.now()))
                )
            ).all()
            tags.extend(m.active_tag for m in app_tag_maps if m.active_tag is not None)
        return tags

    async def _approver_role_ids(self, role_ids: set[str]) -> set[str]:
        if not role_ids:
            return set()
        return set(
            (
                await db.session.scalars(
                    select(OktaUserGroupMember.group_id)
                    .join(RoleGroup, RoleGroup.id == OktaUserGroupMember.group_id)
                    .where(OktaUserGroupMember.group_id.in_(role_ids))
                    .where(OktaUserGroupMember.user_id == self.approver_id)
                    .where(OktaUserGroupMember.is_owner.is_(False))
                    .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
                )
            ).all()
        )
