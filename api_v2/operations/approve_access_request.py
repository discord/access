"""
Approve access request operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic

from api_v2.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api_v2.operations.constraints import CheckForReason
from api_v2.operations.modify_group_users import ModifyGroupUsers
from api_v2.plugins import get_notification_hook
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary, AuditAccessRequestSummary, AuditUserSummary

logger = logging.getLogger(__name__)


class ApproveAccessRequest:
    def __init__(
        self,
        db: Session,
        *,
        access_request: AccessRequest | str,
        approver_user: Optional[OktaUser | str] = None,
        approval_reason: str = "",
        ending_at: Optional[datetime] = None,
        notify: bool = True,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request
        
        self.access_request = (
            self.db.query(AccessRequest)
            .options(joinedload(AccessRequest.active_requested_group))
            .filter(AccessRequest.id == (access_request if isinstance(access_request, str) else access_request.id))
            .first()
        )

        if approver_user is None:
            self.approver_id = None
            self.approver_email = None
        elif isinstance(approver_user, str):
            approver = self.db.get(OktaUser, approver_user)
            self.approver_id = approver.id
            self.approver_email = approver.email
        else:
            self.approver_id = approver_user.id
            self.approver_email = approver_user.email

        self.approval_reason = approval_reason
        self.ending_at = ending_at
        self.notify = notify
        self.notification_hook = get_notification_hook()

    def _log_audit_event(self) -> None:
        """Log the access request approval using structured audit logging."""
        # Get the group with relationships for audit logging
        group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.access_request.requested_group_id)
            .first()
        )

        # Build audit log data
        audit_data = {
            "event_type": AuditEventType.ACCESS_APPROVE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.approver_id,
            "current_user_email": self.approver_email,
        }

        # Extract request information if available
        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For") or
                self.request.headers.get("X-Real-IP") or
                self.request.client.host if self.request.client else None
            )

        # Add group information
        if group:
            group_summary = AuditGroupSummary(
                id=group.id,
                name=group.name,
                type=group.type,
                app=AuditAppSummary(
                    id=group.app.id,
                    name=group.app.name
                ) if hasattr(group, 'app') and group.app else None
            )
            audit_data["group"] = group_summary

        # Add access request information
        if self.access_request:
            request_summary = AuditAccessRequestSummary(
                id=self.access_request.id,
                request_reason=self.access_request.request_reason,
                request_ending_at=self.access_request.request_ending_at,
                request_ownership=self.access_request.request_ownership,
                resolution_reason=self.approval_reason,
                approval_ending_at=self.ending_at,
            )
            audit_data["request"] = request_summary

        # Add requester information
        requester = self.db.get(OktaUser, self.access_request.requester_user_id)
        if requester:
            requester_summary = AuditUserSummary(
                id=requester.id,
                email=requester.email,
                first_name=requester.first_name,
                last_name=requester.last_name,
                display_name=requester.display_name,
            )
            audit_data["requester"] = requester_summary

        # Create structured audit log
        audit_log = AuditLogRead(**audit_data)
        
        # Output structured log as JSON
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self) -> AccessRequest:
        # Don't allow approving a request that is already resolved
        if self.access_request.status != AccessRequestStatus.PENDING or self.access_request.resolved_at is not None:
            return self.access_request

        # Don't allow requester to approve their own request
        if self.access_request.requester_user_id == self.approver_id:
            return self.access_request

        # Don't allow approving a request if the reason is invalid and required
        valid, _ = CheckForReason(
            self.db,
            group=self.access_request.requested_group,
            reason=self.approval_reason,
            members_to_add=[self.access_request.requester_user_id] if not self.access_request.request_ownership else [],
            owners_to_add=[self.access_request.requester_user_id] if self.access_request.request_ownership else [],
        ).execute_for_group()
        if not valid:
            return self.access_request

        # Don't allow approving a request if the requester is deleted
        requester = self.db.get(OktaUser, self.access_request.requester_user_id)
        if requester is None or requester.deleted_at is not None:
            return self.access_request

        # Don't allow approving a request for an a deleted or unmanaged group
        if self.access_request.active_requested_group is None:
            return self.access_request
        if not self.access_request.active_requested_group.is_managed:
            return self.access_request

        # Log audit event
        self._log_audit_event()

        if self.access_request.request_ownership:
            ModifyGroupUsers(
                self.db,
                group=self.access_request.requested_group_id,
                current_user_id=self.approver_id,
                users_added_ended_at=self.ending_at,
                created_reason=self.approval_reason,
                owners_to_add=[self.access_request.requester_user_id],
                notify=self.notify,
                request=self.request,
            ).execute()
        else:
            ModifyGroupUsers(
                self.db,
                group=self.access_request.requested_group_id,
                current_user_id=self.approver_id,
                users_added_ended_at=self.ending_at,
                created_reason=self.approval_reason,
                members_to_add=[self.access_request.requester_user_id],
                notify=self.notify,
                request=self.request,
            ).execute()

        return self.access_request