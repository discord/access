"""
Delete group operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import asyncio
import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic

from api_v2.models import (
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
from api_v2.operations.reject_access_request import RejectAccessRequest
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary
from api_v2.services import okta

logger = logging.getLogger(__name__)


class DeleteGroup:
    def __init__(
        self,
        db: Session,
        *,
        group: OktaGroup | str,
        sync_to_okta: bool = True,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        self.group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
            .first()
        )

        self.sync_to_okta = sync_to_okta

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def _log_audit_event(self) -> None:
        """Log the group deletion audit event."""
        email = None
        if self.current_user_id is not None:
            user = self.db.get(OktaUser, self.current_user_id)
            email = getattr(user, "email", None)

        # Build group summary
        group_summary = AuditGroupSummary(
            id=self.group.id,
            name=self.group.name,
            type=self.group.__class__.__name__,
        )

        # Add app info if this is an AppGroup
        if isinstance(self.group, AppGroup) and self.group.app:
            group_summary.app = AuditAppSummary(
                id=self.group.app.id,
                name=self.group.app.name,
            )

        audit_data = {
            "event_type": AuditEventType.GROUP_DELETE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "group": group_summary,
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For")
                or self.request.headers.get("X-Real-IP")
                or (self.request.client.host if self.request.client else None)
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self) -> None:
        # Run asynchronously to parallelize Okta API requests
        asyncio.run(self._execute())

    async def _execute(self) -> None:
        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        okta_tasks = []

        # Prevent deletion of the Access owner group
        if type(self.group) is AppGroup and self.group.is_owner:
            app = (
                self.db.query(App)
                .filter(App.id == self.group.app_id)
                .filter(App.deleted_at.is_(None))
                .first()
            )
            if app is not None and app.name == App.ACCESS_APP_RESERVED_NAME:
                raise ValueError("Access application owner group cannot be deleted")

        # Audit logging
        self._log_audit_event()

        if self.sync_to_okta:
            okta_tasks.append(asyncio.create_task(okta.async_delete_group(self.group.id)))

        self.group.deleted_at = func.now()

        # End all group members including group members via a role
        group_memberships_query = (
            self.db.query(OktaUserGroupMember)
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .filter(OktaUserGroupMember.group_id == self.group.id)
        )

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

        group_memberships_query.update({OktaUserGroupMember.ended_at: func.now()}, synchronize_session="fetch")

        # End all roles associations where this group was a member
        self.db.query(RoleGroupMap).filter(
            or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now())
        ).filter(RoleGroupMap.group_id == self.group.id).update(
            {RoleGroupMap.ended_at: func.now()}, synchronize_session="fetch"
        )

        if type(self.group) is RoleGroup:
            # End all group memberships via the role grant
            self.db.query(OktaUserGroupMember).filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            ).filter(
                OktaUserGroupMember.role_group_map_id.in_(
                    self.db.query(RoleGroupMap.id)
                    .filter(
                        or_(
                            RoleGroupMap.ended_at.is_(None),
                            RoleGroupMap.ended_at > func.now(),
                        )
                    )
                    .filter(RoleGroupMap.role_group_id == self.group.id)
                )
            ).update(
                {OktaUserGroupMember.ended_at: func.now()},
                synchronize_session="fetch",
            )

            # Check if there are other OktaUserGroupMembers for this user/group
            # combination before removing group membership in Okta, there can be multiple role groups
            # which allow group access for this user
            role_associated_groups_mappings_query = (
                self.db.query(RoleGroupMap)
                .filter(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
                .filter(RoleGroupMap.role_group_id == self.group.id)
            )

            if self.sync_to_okta:
                role_associated_groups_mappings = role_associated_groups_mappings_query.all()

                removed_role_group_users_with_other_access = (
                    self.db.query(OktaUserGroupMember)
                    .with_entities(
                        OktaUserGroupMember.user_id,
                        OktaUserGroupMember.group_id,
                        OktaUserGroupMember.is_owner,
                    )
                    .filter(
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
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
                {RoleGroupMap.ended_at: func.now()}, synchronize_session="fetch"
            )

        self.db.commit()

        # Reject all pending access requests for this group
        obsolete_access_requests = (
            self.db.query(AccessRequest)
            .filter(AccessRequest.requested_group_id == self.group.id)
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for obsolete_access_request in obsolete_access_requests:
            RejectAccessRequest(
                self.db,
                access_request=obsolete_access_request,
                rejection_reason="Closed because the requested group was deleted",
                current_user_id=self.current_user_id,
                request=self.request,
            ).execute()

        # End all tag mappings for this group
        self.db.query(OktaGroupTagMap).filter(
            or_(
                OktaGroupTagMap.ended_at.is_(None),
                OktaGroupTagMap.ended_at > func.now(),
            )
        ).filter(
            OktaGroupTagMap.group_id == self.group.id,
        ).update(
            {OktaGroupTagMap.ended_at: func.now()},
            synchronize_session="fetch",
        )
        self.db.commit()

        if len(okta_tasks) > 0:
            await asyncio.wait(okta_tasks)