import asyncio
from datetime import UTC, datetime
from typing import Dict, Optional, Set

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.models.tag import coalesce_ended_at
from api.operations.constraints import CheckForReason, CheckForSelfAdd
from api.plugins import get_notification_hook
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.schemas import AuditLogSchema, EventType


class ModifyGroupUsers:
    def __init__(
        self,
        *,
        group: OktaGroup | str,
        users_added_ended_at: Optional[datetime] = None,
        members_to_add: list[str] = [],
        owners_to_add: list[str] = [],
        members_should_expire: list[int] = [],
        owners_should_expire: list[int] = [],
        members_to_remove: list[str] = [],
        owners_to_remove: list[str] = [],
        sync_to_okta: bool = True,
        current_user_id: Optional[str] = None,
        created_reason: str = "",
        notify: bool = True,
    ):
        self._group_arg = group
        self._users_added_ended_at_arg = users_added_ended_at
        self._members_to_add_arg = members_to_add
        self._owners_to_add_arg = owners_to_add
        self._members_should_expire_arg = members_should_expire
        self._owners_should_expire_arg = owners_should_expire
        self._members_to_remove_arg = members_to_remove
        self._owners_to_remove_arg = owners_to_remove

        self.sync_to_okta = sync_to_okta

        self._current_user_id_arg = current_user_id

        self.created_reason = created_reason
        self.notify = notify

        self.notification_hook = get_notification_hook()

    async def _resolve(self) -> None:
        group = self._group_arg
        users_added_ended_at = self._users_added_ended_at_arg
        members_to_add = self._members_to_add_arg
        owners_to_add = self._owners_to_add_arg
        members_should_expire = self._members_should_expire_arg
        owners_should_expire = self._owners_should_expire_arg
        members_to_remove = self._members_to_remove_arg
        owners_to_remove = self._owners_to_remove_arg

        self.group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    joinedload(AppGroup.app),
                    selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                )
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == (group if isinstance(group, str) else group.id))
            )
        ).first()

        # Determine the minimum time allowed for group membership and ownership by current group tags
        tags = [tag_map.active_tag for tag_map in self.group.active_group_tags]
        self.members_added_ended_at = coalesce_ended_at(
            constraint_key=Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=users_added_ended_at,
            group_is_managed=self.group.is_managed,
        )
        self.owners_added_ended_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=users_added_ended_at,
            group_is_managed=self.group.is_managed,
        )

        self.members_to_add = []
        if len(members_to_add) > 0:
            self.members_to_add = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.id.in_(members_to_add)).where(OktaUser.deleted_at.is_(None))
                )
            ).all()

        self.owners_to_add = []
        if len(owners_to_add) > 0:
            self.owners_to_add = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.id.in_(owners_to_add)).where(OktaUser.deleted_at.is_(None))
                )
            ).all()

        self.members_should_expire = []
        if len(members_should_expire) > 0:
            self.members_should_expire = (
                await db.session.scalars(
                    select(OktaUserGroupMember)
                    .where(OktaUserGroupMember.id.in_(members_should_expire))
                    .where(OktaUserGroupMember.group_id == self.group.id)
                    .where(OktaUserGroupMember.ended_at > func.now())
                    .where(OktaUserGroupMember.is_owner.is_(False))
                )
            ).all()

        self.owners_should_expire = []
        if len(owners_should_expire) > 0:
            self.owners_should_expire = (
                await db.session.scalars(
                    select(OktaUserGroupMember)
                    .where(OktaUserGroupMember.id.in_(owners_should_expire))
                    .where(OktaUserGroupMember.group_id == self.group.id)
                    .where(OktaUserGroupMember.ended_at > func.now())
                    .where(OktaUserGroupMember.is_owner.is_(True))
                )
            ).all()

        self.members_to_remove = []
        if len(members_to_remove) > 0:
            self.members_to_remove = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.id.in_(members_to_remove)).where(OktaUser.deleted_at.is_(None))
                )
            ).all()

        self.owners_to_remove = []
        if len(owners_to_remove) > 0:
            self.owners_to_remove = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.id.in_(owners_to_remove)).where(OktaUser.deleted_at.is_(None))
                )
            ).all()

        self.current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser)
                    .where(OktaUser.deleted_at.is_(None))
                    .where(OktaUser.id == self._current_user_id_arg)
                )
            ).first(),
            "id",
            None,
        )

    async def execute(self) -> OktaGroup:
        await self._resolve()
        # Fast return if no changes are being made
        if (
            len(self.members_to_add)
            + len(self.members_to_remove)
            + len(self.members_should_expire)
            + len(self.owners_to_add)
            + len(self.owners_to_remove)
            + len(self.owners_should_expire)
            == 0
        ):
            return self.group

        # Check groups tags to see if self-add is allowed
        valid, _ = await CheckForSelfAdd(
            group=self.group,
            current_user=self.current_user_id,
            members_to_add=self.members_to_add,
            owners_to_add=self.owners_to_add,
        ).execute_for_group()
        if not valid:
            return self.group

        # Check group tags to see if a reason is required for adding members or owners
        valid, _ = await CheckForReason(
            group=self.group,
            reason=self.created_reason,
            members_to_add=self.members_to_add,
            owners_to_add=self.owners_to_add,
        ).execute_for_group()
        if not valid:
            return self.group

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, self.current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_modify_users,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "group": self.group,
                    "owners_removed_ids_emails": self.owners_to_remove,
                    "owners_added_ids_emails": self.owners_to_add,
                    "owners_should_expire_user_id_group_id": self.owners_should_expire,
                    "members_removed_ids_emails": self.members_to_remove,
                    "members_added_ids_emails": self.members_to_add,
                    "members_should_expire_user_id_group_id": self.members_should_expire,
                }
            )
        )

        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        async_tasks = []
        # Access requests approved by this operation; their notifications are
        # prepared after the final commit and dispatched alongside async_tasks.
        approved_access_requests: list[AccessRequest] = []

        # First remove all users from the group including those that we wish to add.
        # That way we can easily extend time-bounded group memberships and audit when
        # those extensions occured
        remove_changed_members = self.members_to_remove + self.members_to_add
        remove_changed_owners = self.owners_to_add + self.owners_to_remove

        # Track access overall lost and gained to pass along to app group lifecycle plugin hooks
        members_lost_by_group: dict[OktaGroup | RoleGroup | None, list[OktaUser]] = {}
        members_gained_by_group: dict[OktaGroup | RoleGroup | None, list[OktaUser]] = {}

        # End group memberships and ownerships
        if len(remove_changed_members) > 0 or len(remove_changed_owners) > 0:
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
                .where(OktaUserGroupMember.group_id == self.group.id)
                .where(OktaUserGroupMember.is_owner.is_(False))
                .where(OktaUserGroupMember.user_id.in_([m.id for m in remove_changed_members]))
                .where(OktaUserGroupMember.role_group_map_id.is_(None))
                .values(
                    {OktaUserGroupMember.ended_at: func.now(), OktaUserGroupMember.ended_actor_id: self.current_user_id}
                )
                .execution_options(synchronize_session="fetch")
            )

            await db.session.execute(
                update(OktaUserGroupMember)
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
                .where(OktaUserGroupMember.group_id == self.group.id)
                .where(OktaUserGroupMember.is_owner.is_(True))
                .where(OktaUserGroupMember.user_id.in_([m.id for m in remove_changed_owners]))
                .where(OktaUserGroupMember.role_group_map_id.is_(None))
                .values(
                    {OktaUserGroupMember.ended_at: func.now(), OktaUserGroupMember.ended_actor_id: self.current_user_id}
                )
                .execution_options(synchronize_session="fetch")
            )

            # For role groups, members to be removed should also be removed from all role associated groups
            if type(self.group) is RoleGroup:
                role_associated_groups_mappings = (
                    await db.session.scalars(
                        select(RoleGroupMap)
                        .where(
                            or_(
                                RoleGroupMap.ended_at.is_(None),
                                RoleGroupMap.ended_at > func.now(),
                            )
                        )
                        .where(RoleGroupMap.role_group_id == self.group.id)
                    )
                ).all()
                role_associated_group_memberships = (
                    update(OktaUserGroupMember)
                    .where(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        )
                    )
                    .where(OktaUserGroupMember.user_id.in_([m.id for m in remove_changed_members]))
                    .where(OktaUserGroupMember.role_group_map_id.in_([m.id for m in role_associated_groups_mappings]))
                )

                await db.session.execute(
                    role_associated_group_memberships.values(
                        {
                            OktaUserGroupMember.ended_at: func.now(),
                            OktaUserGroupMember.ended_actor_id: self.current_user_id,
                        }
                    ).execution_options(synchronize_session="fetch")
                )

        # Remove members and owners from Okta groups
        if len(self.members_to_remove) > 0 or len(self.owners_to_remove) > 0:
            # Check if there are other OktaUserGroupMembers for this user/group
            # combination before removing membership, there can be multiple role groups
            # which allow group access for this user
            members_to_remove_ids = [m.id for m in self.members_to_remove]
            owners_to_remove_ids = [m.id for m in self.owners_to_remove]
            removed_users_with_other_access = (
                await db.session.execute(
                    select(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
                    .where(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        )
                    )
                    .where(OktaUserGroupMember.group_id == self.group.id)
                    .where(OktaUserGroupMember.user_id.in_(members_to_remove_ids + owners_to_remove_ids))
                    .group_by(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
                )
            ).all()
            removed_members_with_other_access_ids = [
                m.user_id for m in removed_users_with_other_access if not m.is_owner
            ]
            okta_members_to_remove_ids = set(members_to_remove_ids) - set(removed_members_with_other_access_ids)

            # Track members who lost all access to this group
            members_who_lost_access = [m for m in self.members_to_remove if m.id in okta_members_to_remove_ids]
            if len(members_who_lost_access) > 0:
                members_lost_by_group[self.group] = members_who_lost_access

            if self.sync_to_okta and self.group.is_managed:
                for member_id in okta_members_to_remove_ids:
                    # Remove user from okta group membership if the group is managed by Access
                    async_tasks.append(asyncio.create_task(okta.remove_user_from_group(self.group.id, member_id)))

            removed_owners_with_other_access_ids = [m.user_id for m in removed_users_with_other_access if m.is_owner]
            okta_owners_to_remove_ids = set(owners_to_remove_ids) - set(removed_owners_with_other_access_ids)
            if self.sync_to_okta and self.group.is_managed:
                for owner_id in okta_owners_to_remove_ids:
                    # Remove user from okta group owners if the group is managed by Access
                    # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                    async_tasks.append(asyncio.create_task(okta.remove_owner_from_group(self.group.id, owner_id)))

            # For role groups, members to be removed should also be removed from all role associated groups
            if type(self.group) is RoleGroup:
                role_associated_groups_mappings = (
                    await db.session.scalars(
                        select(RoleGroupMap)
                        .options(joinedload(RoleGroupMap.active_group))
                        .join(RoleGroupMap.active_group)
                        .where(OktaGroup.is_managed.is_(True))
                        .where(
                            or_(
                                RoleGroupMap.ended_at.is_(None),
                                RoleGroupMap.ended_at > func.now(),
                            )
                        )
                        .where(RoleGroupMap.role_group_id == self.group.id)
                    )
                ).all()
                # Check if there are other OktaUserGroupMembers for this user/group
                # combination before removing group membership, there can be multiple role groups
                # which allow group access for this user
                removed_role_group_users_with_other_access = (
                    await db.session.execute(
                        select(
                            OktaUserGroupMember.user_id,
                            OktaUserGroupMember.group_id,
                            OktaUserGroupMember.is_owner,
                        )
                        .where(
                            or_(
                                OktaUserGroupMember.ended_at.is_(None),
                                OktaUserGroupMember.ended_at > func.now(),
                            )
                        )
                        .where(OktaUserGroupMember.user_id.in_(members_to_remove_ids + owners_to_remove_ids))
                        .where(OktaUserGroupMember.group_id.in_([r.group_id for r in role_associated_groups_mappings]))
                        .group_by(
                            OktaUserGroupMember.user_id,
                            OktaUserGroupMember.group_id,
                            OktaUserGroupMember.is_owner,
                        )
                    )
                ).all()
                for role_associated_group_map in role_associated_groups_mappings:
                    if not role_associated_group_map.is_owner:
                        removed_members_with_other_access_ids = [
                            m.user_id
                            for m in removed_role_group_users_with_other_access
                            if role_associated_group_map.group_id == m.group_id and not m.is_owner
                        ]
                        okta_members_to_remove_ids = set(members_to_remove_ids) - set(
                            removed_members_with_other_access_ids
                        )

                        # Track members who lost all access to this role-associated group
                        members_who_lost_access_to_role_group = [
                            m for m in self.members_to_remove if m.id in okta_members_to_remove_ids
                        ]
                        if len(members_who_lost_access_to_role_group) > 0:
                            associated_group = role_associated_group_map.active_group
                            members_lost_by_group[associated_group] = members_who_lost_access_to_role_group

                        # Remove from Okta if sync is enabled
                        if self.sync_to_okta:
                            for member_id in okta_members_to_remove_ids:
                                # Remove user from okta group members
                                async_tasks.append(
                                    asyncio.create_task(
                                        okta.remove_user_from_group(role_associated_group_map.group_id, member_id)
                                    )
                                )
                    else:
                        removed_owners_with_other_access_ids = [
                            m.user_id
                            for m in removed_role_group_users_with_other_access
                            if role_associated_group_map.group_id == m.group_id and m.is_owner
                        ]
                        okta_owners_to_remove_ids = set(owners_to_remove_ids) - set(
                            removed_owners_with_other_access_ids
                        )
                        # Remove from Okta if sync is enabled
                        if self.sync_to_okta:
                            for owner_id in okta_owners_to_remove_ids:
                                # Remove user from okta group owners
                                # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                                async_tasks.append(
                                    asyncio.create_task(
                                        okta.remove_owner_from_group(role_associated_group_map.group_id, owner_id)
                                    )
                                )

            await db.session.commit()

            # Invoke app group lifecycle plugin hooks for removed members
            for group, members in members_lost_by_group.items():
                plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
                if plugin_id is not None:
                    try:
                        hook = get_app_group_lifecycle_hook()
                        hook.group_members_removed(
                            session=db.session, group=group, members=members, plugin_id=plugin_id
                        )
                        await db.session.commit()
                    except Exception:
                        logging.getLogger("api").exception(
                            f"Failed to invoke group_members_removed hook for group {group.id if group else None} with plugin '{plugin_id}'"
                        )
                        await db.session.rollback()

        # Commit all changes so far
        await db.session.commit()

        # Mark relevant OktaUserGroupMembers as 'Should expire'
        # Only relevant for the expiring groups page so not adding checks for this field anywhere else since OK if marked to expire
        # then manually renewed from group page or with an access request
        if len(self.members_should_expire) > 0:
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.id.in_(m.id for m in self.members_should_expire))
                .values({OktaUserGroupMember.should_expire: True})
                .execution_options(synchronize_session="fetch")
            )

        if len(self.owners_should_expire) > 0:
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(OktaUserGroupMember.id.in_(m.id for m in self.owners_should_expire))
                .values({OktaUserGroupMember.should_expire: True})
                .execution_options(synchronize_session="fetch")
            )

        # Commit all changes so far
        await db.session.commit()

        # Add new group members and group owners and add them to Okta
        if len(self.members_to_add) > 0 or len(self.owners_to_add) > 0:
            # Check which members being added currently have NO active memberships (to track first-time access)
            members_to_add_ids = [m.id for m in self.members_to_add]
            existing_members_with_access = (
                await db.session.scalars(
                    select(OktaUserGroupMember.user_id)
                    .where(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        )
                    )
                    .where(OktaUserGroupMember.group_id == self.group.id)
                    .where(OktaUserGroupMember.user_id.in_(members_to_add_ids))
                    .where(OktaUserGroupMember.is_owner.is_(False))
                    .group_by(OktaUserGroupMember.user_id)
                )
            ).all()
            existing_members_with_access_ids = set(existing_members_with_access)
            members_gaining_first_access_ids = set(members_to_add_ids) - existing_members_with_access_ids

            # Track members who are gaining their first access to this group
            if len(members_gaining_first_access_ids) > 0:
                members_gained_by_group[self.group] = [
                    m for m in self.members_to_add if m.id in members_gaining_first_access_ids
                ]

            group_memberships_added: Dict[str, Dict[str, OktaUserGroupMember]] = {self.group.id: {}}
            for member in self.members_to_add:
                if self.sync_to_okta and self.group.is_managed:
                    # Add user to Access-managed okta group members
                    async_tasks.append(asyncio.create_task(okta.add_user_to_group(self.group.id, member.id)))
                membership_to_add = OktaUserGroupMember(
                    user_id=member.id,
                    group_id=self.group.id,
                    is_owner=False,
                    ended_at=self.members_added_ended_at,
                    created_reason=self.created_reason,
                    created_actor_id=self.current_user_id,
                    ended_actor_id=self.current_user_id if self.members_added_ended_at is not None else None,
                )
                group_memberships_added[self.group.id][member.id] = membership_to_add
                db.session.add(membership_to_add)

            group_ownerships_added: Dict[str, Dict[str, OktaUserGroupMember]] = {self.group.id: {}}
            for owner in self.owners_to_add:
                if self.sync_to_okta and self.group.is_managed:
                    # Add user to Access-managed okta group owners
                    # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                    async_tasks.append(asyncio.create_task(okta.add_owner_to_group(self.group.id, owner.id)))
                ownership_to_add = OktaUserGroupMember(
                    user_id=owner.id,
                    group_id=self.group.id,
                    is_owner=True,
                    ended_at=self.owners_added_ended_at,
                    created_reason=self.created_reason,
                    created_actor_id=self.current_user_id,
                    ended_actor_id=self.current_user_id if self.owners_added_ended_at is not None else None,
                )
                group_ownerships_added[self.group.id][owner.id] = ownership_to_add
                db.session.add(ownership_to_add)

            # For role groups, new members should also be added to all Access-managed role associated groups
            if type(self.group) is RoleGroup:
                role_associated_groups_mappings = (
                    await db.session.scalars(
                        select(RoleGroupMap)
                        .options(joinedload(RoleGroupMap.active_group))
                        .join(RoleGroupMap.active_group)
                        .where(OktaGroup.is_managed.is_(True))
                        .where(
                            or_(
                                RoleGroupMap.ended_at.is_(None),
                                RoleGroupMap.ended_at > func.now(),
                            )
                        )
                        .where(RoleGroupMap.role_group_id == self.group.id)
                    )
                ).all()

                # Check which members being added currently have NO active memberships in role-associated groups
                role_associated_group_ids = [m.group_id for m in role_associated_groups_mappings]
                # Build a dict mapping group_id to set of user_ids that already have access
                existing_access_by_group: Dict[str, set[str]] = {}
                if len(role_associated_group_ids) > 0:
                    for group_id in role_associated_group_ids:
                        existing_access_by_group[group_id] = set()

                    existing_role_members_with_access = (
                        await db.session.execute(
                            select(
                                OktaUserGroupMember.user_id,
                                OktaUserGroupMember.group_id,
                            )
                            .where(
                                or_(
                                    OktaUserGroupMember.ended_at.is_(None),
                                    OktaUserGroupMember.ended_at > func.now(),
                                )
                            )
                            .where(OktaUserGroupMember.user_id.in_(members_to_add_ids))
                            .where(OktaUserGroupMember.group_id.in_(role_associated_group_ids))
                            .group_by(OktaUserGroupMember.user_id, OktaUserGroupMember.group_id)
                        )
                    ).all()
                    for row in existing_role_members_with_access:
                        existing_access_by_group[row.group_id].add(row.user_id)

                for role_associated_group_map in role_associated_groups_mappings:
                    role_associated_access_added: Dict[str, OktaUserGroupMember] = {}
                    if role_associated_group_map.is_owner:
                        group_ownerships_added[role_associated_group_map.group_id] = role_associated_access_added
                    else:
                        group_memberships_added[role_associated_group_map.group_id] = role_associated_access_added

                    for member in self.members_to_add:
                        if self.sync_to_okta:
                            if not role_associated_group_map.is_owner:
                                # Add user to okta group members
                                async_tasks.append(
                                    asyncio.create_task(
                                        okta.add_user_to_group(
                                            role_associated_group_map.group_id,
                                            member.id,
                                        )
                                    )
                                )
                            else:
                                # Add user to okta group owners
                                # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                                async_tasks.append(
                                    asyncio.create_task(
                                        okta.add_owner_to_group(
                                            role_associated_group_map.group_id,
                                            member.id,
                                        )
                                    )
                                )
                        # If the both the role associated group and operation input has an end date,
                        # use the earliest of the two for setting the end date for associated group members and owners
                        if role_associated_group_map.ended_at is None:
                            associated_users_ended_at = self.members_added_ended_at
                        elif self.members_added_ended_at is None:
                            associated_users_ended_at = role_associated_group_map.ended_at
                        else:
                            associated_users_ended_at = min(
                                self.members_added_ended_at.replace(tzinfo=UTC),
                                role_associated_group_map.ended_at.replace(tzinfo=UTC),
                            )

                        access_to_add = OktaUserGroupMember(
                            user_id=member.id,
                            group_id=role_associated_group_map.group_id,
                            is_owner=role_associated_group_map.is_owner,
                            role_group_map_id=role_associated_group_map.id,
                            ended_at=associated_users_ended_at,
                            created_reason=self.created_reason,
                            created_actor_id=self.current_user_id,
                            ended_actor_id=self.current_user_id if associated_users_ended_at is not None else None,
                        )
                        role_associated_access_added[member.id] = access_to_add
                        db.session.add(access_to_add)

                    # Track members who are gaining their first access to this role-associated group
                    if not role_associated_group_map.is_owner:
                        members_gaining_first_access_to_role_group_ids = set(
                            members_to_add_ids
                        ) - existing_access_by_group.get(role_associated_group_map.group_id, set())
                        if len(members_gaining_first_access_to_role_group_ids) > 0:
                            associated_group = role_associated_group_map.active_group
                            members_gained_by_group[associated_group] = [
                                m for m in self.members_to_add if m.id in members_gaining_first_access_to_role_group_ids
                            ]

            # Commit changes so far, so we can reference OktaUserGroupMember in approved AccessRequests
            await db.session.commit()

            # Invoke app group lifecycle plugin hooks for added members
            for group, members in members_gained_by_group.items():
                plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
                if plugin_id is not None:
                    try:
                        hook = get_app_group_lifecycle_hook()
                        hook.group_members_added(session=db.session, group=group, members=members, plugin_id=plugin_id)
                        await db.session.commit()
                    except Exception:
                        logging.getLogger("api").exception(
                            f"Failed to invoke group_members_added hook for group {group.id if group else None} with plugin '{plugin_id}'"
                        )
                        await db.session.rollback()

            # Approve any pending access requests for access granted by this operation
            pending_requests_query = (
                select(AccessRequest)
                .options(joinedload(AccessRequest.requested_group))
                .where(AccessRequest.status == AccessRequestStatus.PENDING)
                .where(AccessRequest.resolved_at.is_(None))
            )

            # Find all pending membership access requests to approve for this group
            # and groups associated if this a role group
            pending_member_requests = (
                await db.session.scalars(
                    pending_requests_query.where(AccessRequest.requested_group_id.in_(group_memberships_added.keys()))
                    .where(AccessRequest.requester_user_id.in_([m.id for m in self.members_to_add]))
                    .where(AccessRequest.request_ownership.is_(False))
                )
            ).all()
            for access_request in pending_member_requests:
                approved_access_requests.append(
                    self._approve_access_request(
                        access_request,
                        group_memberships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            # Find all pending ownership requests to approve for this group
            pending_owner_requests = (
                await db.session.scalars(
                    pending_requests_query.where(AccessRequest.requested_group_id == self.group.id)
                    .where(AccessRequest.requester_user_id.in_([m.id for m in self.owners_to_add]))
                    .where(AccessRequest.request_ownership.is_(True))
                )
            ).all()
            for access_request in pending_owner_requests:
                approved_access_requests.append(
                    self._approve_access_request(
                        access_request,
                        group_ownerships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            # Find all pending ownership requests to approve for groups associated if this a role group
            pending_role_associated_owner_requests = (
                await db.session.scalars(
                    pending_requests_query.where(
                        AccessRequest.requested_group_id.in_(set(group_ownerships_added.keys()) - set([self.group.id]))
                    )
                    .where(AccessRequest.requester_user_id.in_([m.id for m in self.members_to_add]))
                    .where(AccessRequest.request_ownership.is_(True))
                )
            ).all()
            for access_request in pending_role_associated_owner_requests:
                approved_access_requests.append(
                    self._approve_access_request(
                        access_request,
                        group_ownerships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            await db.session.commit()

        # Resolve everything the notification hooks need on the main coroutine
        # before spawning tasks: spawned tasks must only perform network I/O,
        # never db.session access (a session cannot be used concurrently, and
        # under async SQLAlchemy that raises rather than interleaving).
        if self.notify:
            for access_request in approved_access_requests:
                group = access_request.requested_group
                # `resolved_at` was assigned func.now() and expired at flush;
                # reload it explicitly so the hook sees a concrete value.
                await db.session.refresh(access_request, attribute_names=["resolved_at"])
                requester = await db.session.get(OktaUser, access_request.requester_user_id)
                approvers = await get_all_possible_request_approvers(access_request)
                async_tasks.append(
                    asyncio.create_task(self._notify_access_request(access_request, group, requester, approvers))
                )

        if len(async_tasks) > 0:
            await asyncio.wait(async_tasks)

        return self.group

    def _approve_access_request(
        self, access_request: AccessRequest, added_okta_user_group_member: OktaUserGroupMember
    ) -> AccessRequest:
        access_request.status = AccessRequestStatus.APPROVED
        access_request.resolved_at = func.now()
        access_request.resolver_user_id = self.current_user_id
        access_request.resolution_reason = self.created_reason
        access_request.approval_ending_at = added_okta_user_group_member.ended_at
        access_request.approved_membership_id = added_okta_user_group_member.id

        return access_request

    async def _notify_access_request(
        self,
        access_request: AccessRequest,
        group: OktaGroup,
        requester: Optional[OktaUser],
        approvers: Set[OktaUser],
    ) -> None:
        self.notification_hook.access_request_completed(
            access_request=access_request,
            group=group,
            requester=requester,
            approvers=approvers,
            notify_requester=True,
        )
