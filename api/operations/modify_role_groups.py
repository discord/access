import asyncio
from datetime import UTC, datetime
from typing import Dict, Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import selectinload

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
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
from api.services import okta
from api.views.schemas import AuditLogSchema, EventType


class ModifyRoleGroups:
    def __init__(
        self,
        role_group: RoleGroup | str,
        groups_added_ended_at: Optional[datetime] = None,
        groups_to_add: list[str] = [],
        owner_groups_to_add: list[str] = [],
        groups_should_expire: list[str] = [],
        owner_groups_should_expire: list[str] = [],
        groups_to_remove: list[str] = [],
        owner_groups_to_remove: list[str] = [],
        sync_to_okta: bool = True,
        current_user_id: Optional[str] = None,
        created_reason: str = "",
        notify: bool = True,
    ):
        if isinstance(role_group, str):
            self.role = (
                RoleGroup.query.filter(RoleGroup.deleted_at.is_(None)).filter(RoleGroup.id == role_group).first()
            )
        else:
            self.role = role_group

        self.groups_added_ended_at = groups_added_ended_at

        self.groups_to_add = []
        if len(groups_to_add) > 0:
            self.groups_to_add = (
                OktaGroup.query.options(
                    selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag)
                )
                .filter(OktaGroup.id.in_(groups_to_add))
                .filter(OktaGroup.is_managed.is_(True))
                .filter(OktaGroup.deleted_at.is_(None))
                # Don't allow Roles to be added as Groups to Roles
                .filter(OktaGroup.type != RoleGroup.__mapper_args__["polymorphic_identity"])
                .all()
            )
        self.owner_groups_to_add = []
        if len(owner_groups_to_add) > 0:
            self.owner_groups_to_add = (
                OktaGroup.query.options(
                    selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag)
                )
                .filter(OktaGroup.id.in_(owner_groups_to_add))
                .filter(OktaGroup.is_managed.is_(True))
                .filter(OktaGroup.deleted_at.is_(None))
                # Don't allow Roles to be added as Groups to Roles
                .filter(OktaGroup.type != RoleGroup.__mapper_args__["polymorphic_identity"])
                .all()
            )

        self.groups_should_expire = []
        if len(groups_should_expire) > 0:
            self.groups_should_expire = (
                RoleGroupMap.query.filter(RoleGroupMap.id.in_(groups_should_expire))
                .filter(RoleGroupMap.ended_at > db.func.now())
                .filter(RoleGroupMap.is_owner.is_(False))
            ).all()

        self.owner_groups_should_expire = []
        if len(owner_groups_should_expire) > 0:
            self.owner_groups_should_expire = (
                RoleGroupMap.query.filter(RoleGroupMap.id.in_(owner_groups_should_expire))
                .filter(RoleGroupMap.ended_at > db.func.now())
                .filter(RoleGroupMap.is_owner.is_(True))
            ).all()

        self.groups_to_remove = []
        if len(groups_to_remove) > 0:
            self.groups_to_remove = (
                OktaGroup.query.filter(OktaGroup.id.in_(groups_to_remove)).filter(OktaGroup.deleted_at.is_(None)).all()
            )

        self.owner_groups_to_remove = []
        if len(owner_groups_to_remove) > 0:
            self.owner_groups_to_remove = (
                OktaGroup.query.filter(OktaGroup.id.in_(owner_groups_to_remove))
                .filter(OktaGroup.deleted_at.is_(None))
                .all()
            )

        self.sync_to_okta = sync_to_okta

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

        self.created_reason = created_reason

        self.notify = notify

        self.notification_hook = get_notification_hook()

    def execute(self) -> RoleGroup:
        # Run asychronously to parallelize Okta API requests
        return asyncio.run(self._execute())

    async def _execute(self) -> RoleGroup:
        # Fast return if no changes are being made
        if (
            len(self.groups_to_add)
            + len(self.groups_to_remove)
            + len(self.groups_should_expire)
            + len(self.owner_groups_to_add)
            + len(self.owner_groups_to_remove)
            + len(self.owner_groups_should_expire)
            == 0
        ):
            return self.role

        # Check group tags on groups being added to see if current user isn't adding themselves as member or owner
        valid, _ = CheckForSelfAdd(
            group=self.role,
            current_user=self.current_user_id,
            members_to_add=[g.id for g in self.groups_to_add],
            owners_to_add=[g.id for g in self.owner_groups_to_add],
        ).execute_for_role()
        if not valid:
            return self.role

        # Check group tags on groups being added to see if a reason is required
        valid, _ = CheckForReason(
            group=self.role,
            reason=self.created_reason,
            members_to_add=[g.id for g in self.groups_to_add],
            owners_to_add=[g.id for g in self.owner_groups_to_add],
        ).execute_for_role()
        if not valid:
            return self.role

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema(exclude=["group.app.id", "group.app.name"]).dumps(
                {
                    "event_type": EventType.role_group_modify,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "role": self.role,
                    "groups_added_ending_at": self.groups_added_ended_at,
                    "owner_groups_removed_ids_names": self.owner_groups_to_remove,
                    "owner_groups_added_ids_names": self.owner_groups_to_add,
                    "owner_groups_should_expire_role_id_group_id": self.owner_groups_should_expire,
                    "groups_removed_ids_names": self.groups_to_remove,
                    "groups_added_ids_names": self.groups_to_add,
                    "groups_should_expire_role_id_group_id": self.groups_should_expire,
                }
            )
        )

        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        async_tasks = []

        # First remove all groups from the role including those that we wish to add.
        # That way we can easily extend time-bounded group memberships and audit when
        # those extensions occured

        # Remove groups from role
        self.__remove_groups_from_role(self.groups_to_remove + self.groups_to_add, False)
        # Remove owner groups from role
        self.__remove_groups_from_role(self.owner_groups_to_remove + self.owner_groups_to_add, True)

        # Remove role members and owners from Okta groups associated with the role
        if len(self.groups_to_remove) > 0 or len(self.owner_groups_to_remove) > 0:
            # Check if there are other OktaUserGroupMembers for this user/group
            # combination before removing role, there can be multiple role groups
            # which allow group access for this user

            active_role_members = (
                OktaUserGroupMember.query.filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
                .filter(OktaUserGroupMember.group_id == self.role.id)
                .filter(OktaUserGroupMember.is_owner.is_(False))
            )

            role_members_to_remove_ids = [m.user_id for m in active_role_members]
            groups_to_remove_ids = [m.id for m in self.groups_to_remove]
            owner_groups_to_remove_ids = [m.id for m in self.owner_groups_to_remove]
            removed_role_group_users_with_other_access = (
                OktaUserGroupMember.query.with_entities(
                    OktaUserGroupMember.user_id,
                    OktaUserGroupMember.group_id,
                    OktaUserGroupMember.is_owner,
                )
                .filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
                .filter(OktaUserGroupMember.user_id.in_(role_members_to_remove_ids))
                .filter(OktaUserGroupMember.group_id.in_(groups_to_remove_ids + owner_groups_to_remove_ids))
                .group_by(
                    OktaUserGroupMember.user_id,
                    OktaUserGroupMember.group_id,
                    OktaUserGroupMember.is_owner,
                )
                .all()
            )

            if self.sync_to_okta:
                for group_id in groups_to_remove_ids:
                    removed_members_with_other_access_ids = [
                        m.user_id
                        for m in removed_role_group_users_with_other_access
                        if group_id == m.group_id and not m.is_owner
                    ]
                    okta_members_to_remove_ids = set(role_members_to_remove_ids) - set(
                        removed_members_with_other_access_ids
                    )
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
        if len(self.groups_should_expire) > 0:
            RoleGroupMap.query.filter(RoleGroupMap.id.in_(m.id for m in self.groups_should_expire)).update(
                {RoleGroupMap.should_expire: True},
                synchronize_session="fetch",
            )

        if len(self.owner_groups_should_expire) > 0:
            RoleGroupMap.query.filter(RoleGroupMap.id.in_(m.id for m in self.owner_groups_should_expire)).update(
                {RoleGroupMap.should_expire: True},
                synchronize_session="fetch",
            )

        # Commit all changes so far
        db.session.commit()

        # Add new groups to role and owner groups to role
        if len(self.groups_to_add) > 0 or len(self.owner_groups_to_add) > 0:
            role_memberships_added: Dict[str, RoleGroupMap] = {}
            for group in self.groups_to_add:
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
            for owner_group in self.owner_groups_to_add:
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
            active_role_memberships = (
                OktaUserGroupMember.query.filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
                .filter(OktaUserGroupMember.group_id == self.role.id)
                .filter(OktaUserGroupMember.is_owner.is_(False))
                .all()
            )
            group_memberships_added: Dict[str, Dict[str, OktaUserGroupMember]] = {}
            for role_associated_group_map in role_memberships_added.values():
                group_memberships_added[role_associated_group_map.group_id] = role_associated_membership_added = {}
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
            pending_requests_query = AccessRequest.query.filter(
                AccessRequest.status == AccessRequestStatus.PENDING
            ).filter(AccessRequest.resolved_at.is_(None))
            active_role_membership_ids = [m.user_id for m in active_role_memberships]

            # Find all pending membership requests to approve for groups added as members via this role
            pending_member_requests = (
                pending_requests_query.filter(AccessRequest.requested_group_id.in_(group_memberships_added.keys()))
                .filter(AccessRequest.requester_user_id.in_(active_role_membership_ids))
                .filter(AccessRequest.request_ownership.is_(False))
                .all()
            )
            for access_request in pending_member_requests:
                async_tasks.append(
                    self._approve_access_request(
                        access_request,
                        group_memberships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            # Find all pending ownership requests to approve for groups added as owners via this role
            pending_role_associated_owner_requests = (
                pending_requests_query.filter(AccessRequest.requested_group_id.in_(group_ownerships_added.keys()))
                .filter(AccessRequest.requester_user_id.in_(active_role_membership_ids))
                .filter(AccessRequest.request_ownership.is_(True))
                .all()
            )
            for access_request in pending_role_associated_owner_requests:
                async_tasks.append(
                    self._approve_access_request(
                        access_request,
                        group_ownerships_added[access_request.requested_group_id][access_request.requester_user_id],
                    )
                )

            # Approve any pending role requests for memberships granted by this operation
            pending_role_requests_query = (
                RoleRequest.query.filter(RoleRequest.status == AccessRequestStatus.PENDING)
                .filter(RoleRequest.resolved_at.is_(None))
                .filter(RoleRequest.requester_role == self.role)
            )

            added_group_ids = [group.id for group in self.groups_to_add]
            pending_role_memberships = (
                pending_role_requests_query.filter(RoleRequest.request_ownership.is_(False))
                .filter(RoleRequest.requested_group_id.in_(added_group_ids))
                .all()
            )
            for role_request in pending_role_memberships:
                async_tasks.append(
                    self._approve_role_request(role_request, role_memberships_added[role_request.requested_group_id])
                )

            # Approve any pending role requests for ownerships granted by this operation
            added_owner_group_ids = [group.id for group in self.owner_groups_to_add]
            pending_role_ownerships = (
                pending_role_requests_query.filter(RoleRequest.request_ownership.is_(True))
                .filter(RoleRequest.requested_group_id.in_(added_owner_group_ids))
                .all()
            )
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
        old_role_associated_groups_mappings = (
            RoleGroupMap.query.filter(
                db.or_(
                    RoleGroupMap.ended_at.is_(None),
                    RoleGroupMap.ended_at > db.func.now(),
                )
            )
            .filter(RoleGroupMap.role_group_id == self.role.id)
            .filter(RoleGroupMap.group_id.in_([g.id for g in groups_to_remove]))
            .filter(RoleGroupMap.is_owner == owner_groups)
            .all()
        )

        # End group memberships via role
        OktaUserGroupMember.query.filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        ).filter(OktaUserGroupMember.role_group_map_id.in_([m.id for m in old_role_associated_groups_mappings])).update(
            {OktaUserGroupMember.ended_at: db.func.now(), OktaUserGroupMember.ended_actor_id: self.current_user_id},
            synchronize_session="fetch",
        )

        # End mappings of role associated group to role
        RoleGroupMap.query.filter(
            db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > db.func.now())
        ).filter(RoleGroupMap.role_group_id == self.role.id).filter(
            RoleGroupMap.group_id.in_([g.id for g in groups_to_remove])
        ).filter(RoleGroupMap.is_owner == owner_groups).update(
            {RoleGroupMap.ended_at: db.func.now(), RoleGroupMap.ended_actor_id: self.current_user_id},
            synchronize_session="fetch",
        )

    def _approve_access_request(
        self, access_request: AccessRequest, added_okta_user_group_member: OktaUserGroupMember
    ) -> asyncio.Task[None]:
        access_request.status = AccessRequestStatus.APPROVED
        access_request.resolved_at = db.func.now()
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
        role_request.resolved_at = db.func.now()
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
