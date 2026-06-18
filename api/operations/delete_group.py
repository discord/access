import asyncio
from typing import Optional

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
)
from api.operations.reject_access_request import RejectAccessRequest
from api.operations.reject_role_request import RejectRoleRequest
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.schemas import AuditLogSchema, EventType


class DeleteGroup:
    def __init__(self, *, group: OktaGroup | str, sync_to_okta: bool = True, current_user_id: Optional[str] = None):
        self._group_arg = group

        self.sync_to_okta = sync_to_okta

        self._current_user_id_arg = current_user_id

    def execute(self) -> None:
        # Run asychronously to parallelize Okta API requests
        asyncio.run(self._execute())

    async def _execute(self) -> None:
        group_arg = self._group_arg
        group = db.session.scalars(
            select(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .where(OktaGroup.id == (group_arg if isinstance(group_arg, str) else group_arg.id))
        ).first()

        current_user_id = getattr(
            db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self._current_user_id_arg)
            ).first(),
            "id",
            None,
        )

        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        okta_tasks = []

        # Prevent deletion of the Access owner group
        if type(group) is AppGroup and group.is_owner:
            app = db.session.scalars(select(App).where(App.id == group.app_id).where(App.deleted_at.is_(None))).first()
            if app is not None and app.name == App.ACCESS_APP_RESERVED_NAME:
                raise ValueError("Access application owner group cannot be deleted")

        # Audit logging
        email = None
        if current_user_id is not None:
            email = getattr(db.session.get(OktaUser, current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_delete,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": current_user_id,
                    "current_user_email": email,
                    "group": group,
                }
            )
        )

        if self.sync_to_okta:
            okta_tasks.append(asyncio.create_task(okta.async_delete_group(group.id)))

        group.deleted_at = func.now()

        # End all group members including group members via a role
        group_memberships_query = (
            select(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.group_id == group.id)
        )

        direct_members_to_remove_ids = [
            m.user_id
            for m in db.session.scalars(
                group_memberships_query.where(OktaUserGroupMember.is_owner.is_(False)).where(
                    OktaUserGroupMember.role_group_map_id.is_(None)
                )
            ).all()
        ]
        direct_owners_to_remove_ids = [
            m.user_id
            for m in db.session.scalars(
                group_memberships_query.where(OktaUserGroupMember.is_owner.is_(True)).where(
                    OktaUserGroupMember.role_group_map_id.is_(None)
                )
            ).all()
        ]

        db.session.execute(
            update(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.group_id == group.id)
            .values({OktaUserGroupMember.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        # End all roles associations where this group was a member
        db.session.execute(
            update(RoleGroupMap)
            .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
            .where(RoleGroupMap.group_id == group.id)
            .values({RoleGroupMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        if type(group) is RoleGroup:
            # End all group memberships via the role grant
            db.session.execute(
                update(OktaUserGroupMember)
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
                .where(
                    OktaUserGroupMember.role_group_map_id.in_(
                        select(RoleGroupMap.id)
                        .where(
                            or_(
                                RoleGroupMap.ended_at.is_(None),
                                RoleGroupMap.ended_at > func.now(),
                            )
                        )
                        .where(RoleGroupMap.role_group_id == group.id)
                    )
                )
                .values({OktaUserGroupMember.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )

            # Check if there are other OktaUserGroupMembers for this user/group
            # combination before removing group membership in Okta, there can be multiple role groups
            # which allow group access for this user
            role_associated_groups_mappings_query = (
                select(RoleGroupMap)
                .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
                .where(RoleGroupMap.role_group_id == group.id)
            )

            if self.sync_to_okta:
                role_associated_groups_mappings = db.session.scalars(role_associated_groups_mappings_query).all()

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
                    .where(OktaUserGroupMember.user_id.in_(direct_members_to_remove_ids + direct_owners_to_remove_ids))
                    .where(OktaUserGroupMember.group_id.in_([r.group_id for r in role_associated_groups_mappings]))
                    .group_by(
                        OktaUserGroupMember.user_id,
                        OktaUserGroupMember.group_id,
                        OktaUserGroupMember.is_owner,
                    )
                ).all()

                for role_associated_group_map in role_associated_groups_mappings:
                    if not role_associated_group_map.is_owner:
                        removed_members_with_other_access_ids = [
                            m.user_id
                            for m in removed_role_group_users_with_other_access
                            if role_associated_group_map.group_id == m.group_id and not m.is_owner
                        ]
                        okta_members_to_remove_ids = set(direct_members_to_remove_ids) - set(
                            removed_members_with_other_access_ids
                        )
                        for member_id in okta_members_to_remove_ids:
                            okta_tasks.append(
                                asyncio.create_task(
                                    okta.async_remove_user_from_group(role_associated_group_map.group_id, member_id)
                                )
                            )
                    else:
                        removed_owners_with_other_access_ids = [
                            m.user_id
                            for m in removed_role_group_users_with_other_access
                            if role_associated_group_map.group_id == m.group_id and m.is_owner
                        ]
                        okta_owners_to_remove_ids = set(direct_owners_to_remove_ids) - set(
                            removed_owners_with_other_access_ids
                        )
                        for owner_id in okta_owners_to_remove_ids:
                            # Remove user from okta group owners
                            # https://help.okta.com/en-us/Content/Topics/identity-governance/group-owner.htm
                            okta_tasks.append(
                                asyncio.create_task(
                                    okta.async_remove_owner_from_group(role_associated_group_map.group_id, owner_id)
                                )
                            )

            # End all group attachments to this role
            db.session.execute(
                update(RoleGroupMap)
                .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
                .where(RoleGroupMap.role_group_id == group.id)
                .values({RoleGroupMap.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )

        db.session.commit()

        # Reject all pending access requests for this group
        obsolete_access_requests = db.session.scalars(
            select(AccessRequest)
            .where(AccessRequest.requested_group_id == group.id)
            .where(AccessRequest.status == AccessRequestStatus.PENDING)
            .where(AccessRequest.resolved_at.is_(None))
        ).all()
        for obsolete_access_request in obsolete_access_requests:
            RejectAccessRequest(
                access_request=obsolete_access_request,
                rejection_reason="Closed because the requested group was deleted",
                current_user_id=current_user_id,
            ).execute()

        # Reject all pending role requests touching this group, either as the
        # requested target or as the requester role.
        obsolete_role_requests = db.session.scalars(
            select(RoleRequest)
            .where(
                or_(
                    RoleRequest.requested_group_id == group.id,
                    RoleRequest.requester_role_id == group.id,
                )
            )
            .where(RoleRequest.status == AccessRequestStatus.PENDING)
            .where(RoleRequest.resolved_at.is_(None))
        ).all()
        for obsolete_role_request in obsolete_role_requests:
            RejectRoleRequest(
                role_request=obsolete_role_request,
                rejection_reason="Closed because a group in this role request was deleted",
                current_user_id=current_user_id,
            ).execute()

        # End all tag mappings for this group
        db.session.execute(
            update(OktaGroupTagMap)
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
            .where(
                OktaGroupTagMap.group_id == group.id,
            )
            .values({OktaGroupTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )
        db.session.commit()

        # Invoke app group lifecycle plugin hook, if configured
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
        if plugin_id is not None:
            try:
                hook = get_app_group_lifecycle_hook()
                hook.group_deleted(session=db.session, group=group, plugin_id=plugin_id)
                db.session.commit()
            except Exception:
                logging.getLogger("api").exception(
                    f"Failed to invoke group_deleted hook for group {group.id} with plugin '{plugin_id}'"
                )
                db.session.rollback()

        if len(okta_tasks) > 0:
            await asyncio.wait(okta_tasks)
