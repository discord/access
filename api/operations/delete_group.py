import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

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
)
from api.operations.reject_access_request import RejectAccessRequest
from api.plugins import get_audit_events_hook
from api.plugins.audit_events import AuditEventEnvelope
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.views.schemas import AuditLogSchema, EventType
from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic


class DeleteGroup:
    def __init__(self, *, group: OktaGroup | str, sync_to_okta: bool = True, current_user_id: Optional[str] = None):
        self.group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
            .first()
        )

        self.sync_to_okta = sync_to_okta

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> None:
        # Run asychronously to parallelize Okta API requests
        asyncio.run(self._execute())

    async def _execute(self) -> None:
        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        okta_tasks = []

        # Prevent deletion of the Access owner group
        if type(self.group) is AppGroup and self.group.is_owner:
            app = App.query.filter(App.id == self.group.app_id).filter(App.deleted_at.is_(None)).first()
            if app is not None and app.name == App.ACCESS_APP_RESERVED_NAME:
                raise ValueError("Access application owner group cannot be deleted")

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_delete,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "group": self.group,
                }
            )
        )

        if self.sync_to_okta:
            okta_tasks.append(asyncio.create_task(okta.async_delete_group(self.group.id)))

        self.group.deleted_at = db.func.now()

        # End all group members including group members via a role
        group_memberships_query = OktaUserGroupMember.query.filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        ).filter(OktaUserGroupMember.group_id == self.group.id)

        direct_members_to_remove_ids = [
            m.user_id
            for m in group_memberships_query.filter(OktaUserGroupMember.is_owner.is_(False))
            .filter(OktaUserGroupMember.role_group_map_id.is_(None))
            .all()
        ]
        direct_owners_to_remove_ids = [
            m.user_id
            for m in group_memberships_query.filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(OktaUserGroupMember.role_group_map_id.is_(None))
            .all()
        ]

        group_memberships_query.update({OktaUserGroupMember.ended_at: db.func.now()}, synchronize_session="fetch")

        # End all roles associations where this group was a member
        RoleGroupMap.query.filter(
            db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > db.func.now())
        ).filter(RoleGroupMap.group_id == self.group.id).update(
            {RoleGroupMap.ended_at: db.func.now()}, synchronize_session="fetch"
        )

        if type(self.group) is RoleGroup:
            # End all group memberships via the role grant
            OktaUserGroupMember.query.filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            ).filter(
                OktaUserGroupMember.role_group_map_id.in_(
                    db.session.query(RoleGroupMap.id)
                    .filter(
                        db.or_(
                            RoleGroupMap.ended_at.is_(None),
                            RoleGroupMap.ended_at > db.func.now(),
                        )
                    )
                    .filter(RoleGroupMap.role_group_id == self.group.id)
                )
            ).update(
                {OktaUserGroupMember.ended_at: db.func.now()},
                synchronize_session="fetch",
            )

            # Check if there are other OktaUserGroupMembers for this user/group
            # combination before removing group membership in Okta, there can be multiple role groups
            # which allow group access for this user
            role_associated_groups_mappings_query = RoleGroupMap.query.filter(
                db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > db.func.now())
            ).filter(RoleGroupMap.role_group_id == self.group.id)

            if self.sync_to_okta:
                role_associated_groups_mappings = role_associated_groups_mappings_query.all()

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
                    .filter(OktaUserGroupMember.user_id.in_(direct_members_to_remove_ids + direct_owners_to_remove_ids))
                    .filter(OktaUserGroupMember.group_id.in_([r.group_id for r in role_associated_groups_mappings]))
                    .group_by(
                        OktaUserGroupMember.user_id,
                        OktaUserGroupMember.group_id,
                        OktaUserGroupMember.is_owner,
                    )
                    .all()
                )

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
            role_associated_groups_mappings_query.update(
                {RoleGroupMap.ended_at: db.func.now()}, synchronize_session="fetch"
            )

        db.session.commit()

        # Reject all pending access requests for this group
        obsolete_access_requests = (
            AccessRequest.query.filter(AccessRequest.requested_group_id == self.group.id)
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for obsolete_access_request in obsolete_access_requests:
            RejectAccessRequest(
                access_request=obsolete_access_request,
                rejection_reason="Closed because the requested group was deleted",
                current_user_id=self.current_user_id,
            ).execute()

        # End all tag mappings for this group
        OktaGroupTagMap.query.filter(
            db.or_(
                OktaGroupTagMap.ended_at.is_(None),
                OktaGroupTagMap.ended_at > db.func.now(),
            )
        ).filter(
            OktaGroupTagMap.group_id == self.group.id,
        ).update(
            {OktaGroupTagMap.ended_at: db.func.now()},
            synchronize_session="fetch",
        )
        db.session.commit()

        # Invoke app group lifecycle plugin hook, if configured
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)
        if plugin_id is not None:
            try:
                hook = get_app_group_lifecycle_hook()
                hook.group_deleted(session=db.session, group=self.group, plugin_id=plugin_id)
                db.session.commit()
            except Exception:
                current_app.logger.exception(
                    f"Failed to invoke group_deleted hook for group {self.group.id} with plugin '{plugin_id}'"
                )
                db.session.rollback()

        # Emit audit event to plugins (after DB commit)
        try:
            email = None
            if self.current_user_id:
                actor_user = db.session.get(OktaUser, self.current_user_id)
                email = actor_user.email if actor_user else None

            context = has_request_context()
            audit_hook = get_audit_events_hook()
            envelope = AuditEventEnvelope(
                id=uuid4(),
                event_type="group_delete",
                timestamp=datetime.now(timezone.utc),
                actor_id=self.current_user_id or "system",
                actor_email=email,
                target_type="group",
                target_id=self.group.id,
                target_name=self.group.name,
                action="deleted",
                reason="",
                payload={
                    "group_id": self.group.id,
                    "group_name": self.group.name,
                    "group_type": type(self.group).__name__,
                },
                metadata={
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip_address": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                },
            )
            audit_hook.audit_event_logged(envelope=envelope)
        except Exception as e:
            current_app.logger.error(f"Failed to emit audit event: {e}", exc_info=True)

        if len(okta_tasks) > 0:
            await asyncio.wait(okta_tasks)
