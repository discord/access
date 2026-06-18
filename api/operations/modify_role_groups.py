import asyncio
from datetime import UTC, datetime
from typing import Dict, Optional

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
    RoleRequest,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.models.tag import coalesce_ended_at
from api.operations.constraints import CheckForReason, CheckForSelfAdd
from api.plugins import get_notification_hook
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.schemas import AuditLogSchema, EventType


class ModifyRoleGroups:
    def __init__(
        self,
        role_group: RoleGroup | str,
        groups_added_ended_at: Optional[datetime] = None,
        groups_to_add: list[str] = [],
        owner_groups_to_add: list[str] = [],
        groups_should_expire: list[int] = [],
        owner_groups_should_expire: list[int] = [],
        groups_to_remove: list[str] = [],
        owner_groups_to_remove: list[str] = [],
        sync_to_okta: bool = True,
        current_user_id: Optional[str] = None,
        created_reason: str = "",
        notify: bool = True,
    ):
        self._role_group_arg = role_group

        self.groups_added_ended_at = groups_added_ended_at

        self._groups_to_add_arg = groups_to_add
        self._owner_groups_to_add_arg = owner_groups_to_add
        self._groups_should_expire_arg = groups_should_expire
        self._owner_groups_should_expire_arg = owner_groups_should_expire
        self._groups_to_remove_arg = groups_to_remove
        self._owner_groups_to_remove_arg = owner_groups_to_remove

        self.sync_to_okta = sync_to_okta

        self._current_user_id_arg = current_user_id

        self.created_reason = created_reason

        self.notify = notify

        self.notification_hook = get_notification_hook()

    def execute(self) -> RoleGroup:
        # Run asychronously to parallelize Okta API requests
        return asyncio.run(self._execute())

    async def _execute(self) -> RoleGroup:
        role_group_arg = self._role_group_arg
        groups_to_add_arg = self._groups_to_add_arg
        owner_groups_to_add_arg = self._owner_groups_to_add_arg
        groups_should_expire_arg = self._groups_should_expire_arg
        owner_groups_should_expire_arg = self._owner_groups_should_expire_arg
        groups_to_remove_arg = self._groups_to_remove_arg
        owner_groups_to_remove_arg = self._owner_groups_to_remove_arg

        if isinstance(role_group_arg, str):
            self.role = db.session.scalars(
                select(RoleGroup).where(RoleGroup.deleted_at.is_(None)).where(RoleGroup.id == role_group_arg)
            ).first()
        else:
            self.role = role_group_arg

        groups_to_add: list[OktaGroup] = []
        if len(groups_to_add_arg) > 0:
            groups_to_add = db.session.scalars(
                select(OktaGroup)
                .options(
                    selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                    selectin_polymorphic(OktaGroup, [AppGroup]),
                    joinedload(AppGroup.app),
                )
                .where(OktaGroup.id.in_(groups_to_add_arg))
                .where(OktaGroup.is_managed.is_(True))
                .where(OktaGroup.deleted_at.is_(None))
                # Don't allow Roles to be added as Groups to Roles
                .where(OktaGroup.type != RoleGroup.__mapper_args__["polymorphic_identity"])
            ).all()
        owner_groups_to_add: list[OktaGroup] = []
        if len(owner_groups_to_add_arg) > 0:
            owner_groups_to_add = db.session.scalars(
                select(OktaGroup)
                .options(selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag))
                .where(OktaGroup.id.in_(owner_groups_to_add_arg))
                .where(OktaGroup.is_managed.is_(True))
                .where(OktaGroup.deleted_at.is_(None))
                # Don't allow Roles to be added as Groups to Roles
                .where(OktaGroup.type != RoleGroup.__mapper_args__["polymorphic_identity"])
            ).all()

        groups_should_expire: list[RoleGroupMap] = []
        if len(groups_should_expire_arg) > 0:
            groups_should_expire = db.session.scalars(
                select(RoleGroupMap)
                .where(RoleGroupMap.id.in_(groups_should_expire_arg))
                .where(RoleGroupMap.role_group_id == self.role.id)
                .where(RoleGroupMap.ended_at > func.now())
                .where(RoleGroupMap.is_owner.is_(False))
            ).all()

        owner_groups_should_expire: list[RoleGroupMap] = []
        if len(owner_groups_should_expire_arg) > 0:
            owner_groups_should_expire = db.session.scalars(
                select(RoleGroupMap)
                .where(RoleGroupMap.id.in_(owner_groups_should_expire_arg))
                .where(RoleGroupMap.role_group_id == self.role.id)
                .where(RoleGroupMap.ended_at > func.now())
                .where(RoleGroupMap.is_owner.is_(True))
            ).all()

        groups_to_remove: list[OktaGroup] = []
        if len(groups_to_remove_arg) > 0:
            groups_to_remove = db.session.scalars(
                select(OktaGroup)
                # `app` is eager-loaded so the app-group-lifecycle hook path below
                # can read `group.app` without tripping `lazy="raise_on_sql"`.
                .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.id.in_(groups_to_remove_arg))
                .where(OktaGroup.deleted_at.is_(None))
            ).all()

        owner_groups_to_remove: list[OktaGroup] = []
        if len(owner_groups_to_remove_arg) > 0:
            owner_groups_to_remove = db.session.scalars(
                select(OktaGroup)
                .where(OktaGroup.id.in_(owner_groups_to_remove_arg))
                .where(OktaGroup.deleted_at.is_(None))
            ).all()

        self.current_user_id = getattr(
            db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self._current_user_id_arg)
            ).first(),
            "id",
            None,
        )

        # Fast return if no changes are being made
        if (
            len(groups_to_add)
            + len(groups_to_remove)
            + len(groups_should_expire)
            + len(owner_groups_to_add)
            + len(owner_groups_to_remove)
            + len(owner_groups_should_expire)
            == 0
        ):
            return self.role

        # Check group tags on groups being added to see if current user isn't adding themselves as member or owner
        valid, _ = CheckForSelfAdd(
            group=self.role,
            current_user=self.current_user_id,
            members_to_add=[g.id for g in groups_to_add],
            owners_to_add=[g.id for g in owner_groups_to_add],
        ).execute_for_role()
        if not valid:
            return self.role

        # Check group tags on groups being added to see if a reason is required
        valid, _ = CheckForReason(
            group=self.role,
            reason=self.created_reason,
            members_to_add=[g.id for g in groups_to_add],
            owners_to_add=[g.id for g in owner_groups_to_add],
        ).execute_for_role()
        if not valid:
            return self.role

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema(exclude=["group.app.id", "group.app.name"]).dumps(
                {
                    "event_type": EventType.role_group_modify,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "role": self.role,
                    "groups_added_ending_at": self.groups_added_ended_at,
                    "owner_groups_removed_ids_names": owner_groups_to_remove,
                    "owner_groups_added_ids_names": owner_groups_to_add,
                    "owner_groups_should_expire_role_id_group_id": owner_groups_should_expire,
                    "groups_removed_ids_names": groups_to_remove,
                    "groups_added_ids_names": groups_to_add,
                    "groups_should_expire_role_id_group_id": groups_should_expire,
                }
            )
        )

        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        async_tasks = []

        # First remove all groups from the role including those that we wish to add.
        # That way we can easily extend time-bounded group memberships and audit when
        # those extensions occured

        # Remove groups from role
        self.__remove_groups_from_role(groups_to_remove + groups_to_add, False)
        # Remove owner groups from role
        self.__remove_groups_from_role(owner_groups_to_remove + owner_groups_to_add, True)

        # Remove role members and owners from Okta groups associated with the role
        if len(groups_to_remove) > 0 or len(owner_groups_to_remove) > 0:
            # Check if there are other OktaUserGroupMembers for this user/group
            # combination before removing role, there can be multiple role groups
            # which allow group access for this user

            active_role_members = db.session.scalars(
                select(OktaUserGroupMember)
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
                .where(OktaUserGroupMember.group_id == self.role.id)
                .where(OktaUserGroupMember.is_owner.is_(False))
            ).all()

            role_members_to_remove_ids = [m.user_id for m in active_role_members]
            groups_to_remove_ids = [m.id for m in groups_to_remove]
            owner_groups_to_remove_ids = [m.id for m in owner_groups_to_remove]
            removed_role_group_users_with_other_access = db.session.execute(
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
                .where(OktaUserGroupMember.user_id.in_(role_members_to_remove_ids))
                .where(OktaUserGroupMember.group_id.in_(groups_to_remove_ids + owner_groups_to_remove_ids))
                .group_by(
                    OktaUserGroupMember.user_id,
                    OktaUserGroupMember.group_id,
                    OktaUserGroupMember.is_owner,
                )
            ).all()

            groups_to_remove_by_id = {group.id: group for group in groups_to_remove}
            for group_id in groups_to_remove_ids:
                removed_members_with_other_access_ids = [
                    m.user_id
                    for m in removed_role_group_users_with_other_access
                    if group_id == m.group_id and not m.is_owner
                ]
                okta_members_to_remove_ids = set(role_members_to_remove_ids) - set(
                    removed_members_with_other_access_ids
                )

                # Invoke app group lifecycle plugin hooks for removed members
                if len(okta_members_to_remove_ids) > 0:
                    # Use the eager-loaded group (with `app`) rather than a bare
                    # db.session.get, so the hook path can read `group.app`.
                    group = groups_to_remove_by_id[group_id]
                    plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
                    if plugin_id is not None:
                        members_losing_access = db.session.scalars(
                            select(OktaUser)
                            .where(OktaUser.id.in_(okta_members_to_remove_ids))
                            .where(OktaUser.deleted_at.is_(None))
                        ).all()
                        try:
                            hook = get_app_group_lifecycle_hook()
                            hook.group_members_removed(
                                session=db.session, group=group, members=members_losing_access, plugin_id=plugin_id
                            )
                            db.session.commit()
                        except Exception:
                            logging.getLogger("api").exception(
                                f"Failed to invoke group_members_removed hook for group {group.id} with plugin '{plugin_id}'"
                            )
                            db.session.rollback()

                if self.sync_to_okta:
                    for member_id in okta_members_to_remove_ids:
                        # Remove user from okta group members
                        async_tasks.append(asyncio.create_task(okta.async_remove_user_from_group(group_id, member_id)))

            if self.sync_to_okta:
                for group_id in owner_groups_to_remove_ids:
                    removed_owners_with_other_access_ids = [
                        m.user_id
                        for m in removed_role_group_users_with_other_access
                        if group_id == m.group_id and m.is_owner
                    ]
                    okta_owners_to_remove_ids = set(role_members_to_remove_ids) - set(
                        removed_owners_with_other_access_ids
                    )
                    for owner_id in okta_owners_to_remove_ids:
                        # Remove user from okta group owners
                        # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                        async_tasks.append(asyncio.create_task(okta.async_remove_owner_from_group(group_id, owner_id)))

        # Mark relevant role memberships and ownerships as 'Should expire'
        # Only relevant for the expiring roles page so not adding checks for this field anywhere else since OK if marked to expire
        # then manually renewed from group/role page or with an access request
        if len(groups_should_expire) > 0:
            db.session.execute(
                update(RoleGroupMap)
                .where(RoleGroupMap.id.in_(m.id for m in groups_should_expire))
                .values({RoleGroupMap.should_expire: True})
                .execution_options(synchronize_session="fetch")
            )

        if len(owner_groups_should_expire) > 0:
            db.session.execute(
                update(RoleGroupMap)
                .where(RoleGroupMap.id.in_(m.id for m in owner_groups_should_expire))
                .values({RoleGroupMap.should_expire: True})
                .execution_options(synchronize_session="fetch")
            )

        # Commit all changes so far
        db.session.commit()

        # Add new groups to role and owner groups to role
        if len(groups_to_add) > 0 or len(owner_groups_to_add) > 0:
            role_memberships_added: Dict[str, RoleGroupMap] = {}
            for group in groups_to_add:
                # Handle group time limit constraints when roles are added to groups
                # with tagged time limits as members
                membership_ended_at = coalesce_ended_at(
                    constraint_key=Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY,
                    tags=[tag_map.active_tag for tag_map in group.active_group_tags],
                    initial_ended_at=self.groups_added_ended_at,
                    group_is_managed=group.is_managed,
                )
                membership_to_add = RoleGroupMap(
                    group_id=group.id,
                    role_group_id=self.role.id,
                    is_owner=False,
                    ended_at=membership_ended_at,
                    created_actor_id=self.current_user_id,
                    ended_actor_id=self.current_user_id if self.groups_added_ended_at is not None else None,
                    created_reason=self.created_reason,
                )
                role_memberships_added[group.id] = membership_to_add
                db.session.add(membership_to_add)

            role_ownerships_added: Dict[str, RoleGroupMap] = {}
            for owner_group in owner_groups_to_add:
                # Handle group time limit constraints when roles are added to groups
                # with tagged time limits as owners
                ownership_ended_at = coalesce_ended_at(
                    constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
                    tags=[tag_map.active_tag for tag_map in owner_group.active_group_tags],
                    initial_ended_at=self.groups_added_ended_at,
                    group_is_managed=owner_group.is_managed,
                )
                ownership_to_add = RoleGroupMap(
                    group_id=owner_group.id,
                    role_group_id=self.role.id,
                    is_owner=True,
                    ended_at=ownership_ended_at,
                    created_actor_id=self.current_user_id,
                    ended_actor_id=self.current_user_id if self.groups_added_ended_at is not None else None,
                    created_reason=self.created_reason,
                )
                role_ownerships_added[owner_group.id] = ownership_to_add
                db.session.add(ownership_to_add)

            # Commit changes so far so we can reference the ids of the new role group maps in the OktaUserGroupMembers
            db.session.commit()

            # Group members of a role should be added as members to all newly added groups
            # and owner groups associated with that role
            active_role_memberships = db.session.scalars(
                select(OktaUserGroupMember)
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
                .where(OktaUserGroupMember.group_id == self.role.id)
                .where(OktaUserGroupMember.is_owner.is_(False))
            ).all()
            groups_added_by_id = {group.id: group for group in groups_to_add}
            group_memberships_added: Dict[str, Dict[str, OktaUserGroupMember]] = {}
            for role_associated_group_map in role_memberships_added.values():
                group_memberships_added[role_associated_group_map.group_id] = role_associated_membership_added = {}

                # Check which members being added currently have NO active memberships (to track first-time access)
                members_to_add_ids = [m.user_id for m in active_role_memberships]
                existing_members_with_access = db.session.scalars(
                    select(OktaUserGroupMember.user_id)
                    .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
                    .where(OktaUserGroupMember.group_id == role_associated_group_map.group_id)
                    .where(OktaUserGroupMember.user_id.in_(members_to_add_ids))
                    .where(OktaUserGroupMember.is_owner.is_(False))
                    .group_by(OktaUserGroupMember.user_id)
                ).all()
                existing_member_ids = set(existing_members_with_access)
                members_gaining_access_ids = set(members_to_add_ids) - existing_member_ids

                # Invoke app group lifecycle plugin hooks for added members
                if len(members_gaining_access_ids) > 0:
                    group = groups_added_by_id[role_associated_group_map.group_id]
                    plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
                    if plugin_id is not None:
                        members_gaining_access = db.session.scalars(
                            select(OktaUser)
                            .where(OktaUser.id.in_(members_gaining_access_ids))
                            .where(OktaUser.deleted_at.is_(None))
                        ).all()
                        try:
                            hook = get_app_group_lifecycle_hook()
                            hook.group_members_added(
                                session=db.session, group=group, members=members_gaining_access, plugin_id=plugin_id
                            )
                            db.session.commit()
                        except Exception:
                            logging.getLogger("api").exception(
                                f"Failed to invoke group_members_added hook for group {group.id} with plugin '{plugin_id}'"
                            )
                            db.session.rollback()

                for member in active_role_memberships:
                    # Add user to okta group members
                    if self.sync_to_okta:
                        async_tasks.append(
                            asyncio.create_task(
                                okta.async_add_user_to_group(role_associated_group_map.group_id, member.user_id)
                            )
                        )

                    # If the both the role membership and role group map are time bounded,
                    # use the earliest of the two for setting the end date for associated group members and owners
                    if role_associated_group_map.ended_at is None:
                        associated_users_ended_at = member.ended_at
                    elif member.ended_at is None:
                        associated_users_ended_at = role_associated_group_map.ended_at
                    else:
                        associated_users_ended_at = min(
                            member.ended_at.replace(tzinfo=UTC), role_associated_group_map.ended_at.replace(tzinfo=UTC)
                        )

                    membership_to_add = OktaUserGroupMember(
                        user_id=member.user_id,
                        group_id=role_associated_group_map.group_id,
                        is_owner=role_associated_group_map.is_owner,
                        role_group_map_id=role_associated_group_map.id,
                        ended_at=associated_users_ended_at,
                        created_actor_id=self.current_user_id,
                        ended_actor_id=self.current_user_id if self.groups_added_ended_at is not None else None,
                        created_reason=self.created_reason,
                    )
                    role_associated_membership_added[member.user_id] = membership_to_add
                    db.session.add(membership_to_add)

            group_ownerships_added: Dict[str, Dict[str, OktaUserGroupMember]] = {}
            for role_associated_group_map in role_ownerships_added.values():
                group_ownerships_added[role_associated_group_map.group_id] = role_associated_ownership_added = {}
                for member in active_role_memberships:
                    # Add user to okta group owners
                    # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                    if self.sync_to_okta:
                        async_tasks.append(
                            asyncio.create_task(
                                okta.async_add_owner_to_group(role_associated_group_map.group_id, member.user_id)
                            )
                        )
                    # If the both the role membership and role group map are time bounded,
                    # use the earliest of the two for setting the end date for associated group members and owners
                    if role_associated_group_map.ended_at is None:
                        associated_users_ended_at = member.ended_at
                    elif member.ended_at is None:
                        associated_users_ended_at = role_associated_group_map.ended_at
                    else:
                        associated_users_ended_at = min(
                            member.ended_at.replace(tzinfo=UTC), role_associated_group_map.ended_at.replace(tzinfo=UTC)
                        )
                    ownership_to_add = OktaUserGroupMember(
                        user_id=member.user_id,
                        group_id=role_associated_group_map.group_id,
                        is_owner=role_associated_group_map.is_owner,
                        role_group_map_id=role_associated_group_map.id,
                        ended_at=associated_users_ended_at,
                        created_actor_id=self.current_user_id,
                        ended_actor_id=self.current_user_id if self.groups_added_ended_at is not None else None,
                        created_reason=self.created_reason,
                    )
                    role_associated_ownership_added[member.user_id] = ownership_to_add
                    db.session.add(ownership_to_add)

            # Commit changes so far, so we can reference OktaUserGroupMember in approved AccessRequests
            db.session.commit()

            # Approve any pending access requests for access granted by this operation
            pending_requests_query = (
                select(AccessRequest)
                .where(AccessRequest.status == AccessRequestStatus.PENDING)
                .where(AccessRequest.resolved_at.is_(None))
            )
            active_role_membership_ids = [m.user_id for m in active_role_memberships]

            # Find all pending membership requests to approve for groups added as members via this role
            pending_member_requests = db.session.scalars(
                pending_requests_query.where(AccessRequest.requested_group_id.in_(group_memberships_added.keys()))
                .where(AccessRequest.requester_user_id.in_(active_role_membership_ids))
                .where(AccessRequest.request_ownership.is_(False))
            ).all()
            for access_request in pending_member_requests:
                async_tasks.append(
                    self._approve_access_request(
                        access_request,
                        group_memberships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            # Find all pending ownership requests to approve for groups added as owners via this role
            pending_role_associated_owner_requests = db.session.scalars(
                pending_requests_query.where(AccessRequest.requested_group_id.in_(group_ownerships_added.keys()))
                .where(AccessRequest.requester_user_id.in_(active_role_membership_ids))
                .where(AccessRequest.request_ownership.is_(True))
            ).all()
            for access_request in pending_role_associated_owner_requests:
                async_tasks.append(
                    self._approve_access_request(
                        access_request,
                        group_ownerships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            # Approve any pending role requests for memberships granted by this operation
            pending_role_requests_query = (
                select(RoleRequest)
                .where(RoleRequest.status == AccessRequestStatus.PENDING)
                .where(RoleRequest.resolved_at.is_(None))
                .where(RoleRequest.requester_role == self.role)
            )

            added_group_ids = [group.id for group in groups_to_add]
            pending_role_memberships = db.session.scalars(
                pending_role_requests_query.where(RoleRequest.request_ownership.is_(False)).where(
                    RoleRequest.requested_group_id.in_(added_group_ids)
                )
            ).all()
            for role_request in pending_role_memberships:
                async_tasks.append(
                    self._approve_role_request(role_request, role_memberships_added[role_request.requested_group_id])
                )

            # Approve any pending role requests for ownerships granted by this operation
            added_owner_group_ids = [group.id for group in owner_groups_to_add]
            pending_role_ownerships = db.session.scalars(
                pending_role_requests_query.where(RoleRequest.request_ownership.is_(True)).where(
                    RoleRequest.requested_group_id.in_(added_owner_group_ids)
                )
            ).all()
            for role_request in pending_role_ownerships:
                async_tasks.append(
                    self._approve_role_request(role_request, role_ownerships_added[role_request.requested_group_id])
                )

            db.session.commit()

        # Commit all changes
        db.session.commit()

        if len(async_tasks) > 0:
            await asyncio.wait(async_tasks)

        return self.role

    def __remove_groups_from_role(self, groups_to_remove: list[OktaGroup] = [], owner_groups: bool = False) -> None:
        if len(groups_to_remove) == 0:
            return

        # Role user members should be removed from any groups associated with that role being removed
        old_role_associated_groups_mappings = db.session.scalars(
            select(RoleGroupMap)
            .where(
                or_(
                    RoleGroupMap.ended_at.is_(None),
                    RoleGroupMap.ended_at > func.now(),
                )
            )
            .where(RoleGroupMap.role_group_id == self.role.id)
            .where(RoleGroupMap.group_id.in_([g.id for g in groups_to_remove]))
            .where(RoleGroupMap.is_owner == owner_groups)
        ).all()

        # End group memberships via role
        db.session.execute(
            update(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.role_group_map_id.in_([m.id for m in old_role_associated_groups_mappings]))
            .values(
                {OktaUserGroupMember.ended_at: func.now(), OktaUserGroupMember.ended_actor_id: self.current_user_id}
            )
            .execution_options(synchronize_session="fetch")
        )

        # End mappings of role associated group to role
        db.session.execute(
            update(RoleGroupMap)
            .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
            .where(RoleGroupMap.role_group_id == self.role.id)
            .where(RoleGroupMap.group_id.in_([g.id for g in groups_to_remove]))
            .where(RoleGroupMap.is_owner == owner_groups)
            .values({RoleGroupMap.ended_at: func.now(), RoleGroupMap.ended_actor_id: self.current_user_id})
            .execution_options(synchronize_session="fetch")
        )

    def _approve_access_request(
        self, access_request: AccessRequest, added_okta_user_group_member: OktaUserGroupMember
    ) -> asyncio.Task[None]:
        access_request.status = AccessRequestStatus.APPROVED
        access_request.resolved_at = func.now()
        access_request.resolver_user_id = self.current_user_id
        access_request.resolution_reason = self.created_reason
        access_request.approval_ending_at = added_okta_user_group_member.ended_at
        access_request.approved_membership_id = added_okta_user_group_member.id

        return asyncio.create_task(self._notify_access_request(access_request))

    async def _notify_access_request(self, access_request: AccessRequest) -> None:
        requester = db.session.get(OktaUser, access_request.requester_user_id)

        approvers = get_all_possible_request_approvers(access_request)

        self.notification_hook.access_request_completed(
            access_request=access_request,
            group=access_request.requested_group,
            requester=requester,
            approvers=approvers,
            notify_requester=True,
        )

    def _approve_role_request(
        self, role_request: RoleRequest, added_role_group_map: RoleGroupMap
    ) -> asyncio.Task[None]:
        role_request.status = AccessRequestStatus.APPROVED
        role_request.resolved_at = func.now()
        role_request.resolver_user_id = self.current_user_id
        role_request.resolution_reason = self.created_reason
        role_request.approval_ending_at = added_role_group_map.ended_at
        role_request.approved_membership_id = added_role_group_map.id

        return asyncio.create_task(self._notify_role_request(role_request))

    async def _notify_role_request(self, role_request: RoleRequest) -> None:
        if not self.notify:
            return

        requester = db.session.get(OktaUser, role_request.requester_user_id)

        approvers = get_all_possible_request_approvers(role_request)

        self.notification_hook.access_role_request_completed(
            role_request=role_request,
            role=role_request.requester_role,
            group=role_request.requested_group,
            requester=requester,
            approvers=approvers,
            notify_requester=True,
        )
