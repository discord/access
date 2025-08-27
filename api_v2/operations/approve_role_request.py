import logging
from datetime import datetime
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic

from api_v2.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api_v2.operations.constraints import CheckForReason
from api_v2.operations.modify_role_groups import ModifyRoleGroups
from api_v2.plugins import get_notification_hook
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary, AuditRoleRequestSummary, AuditRoleGroupSummary, AuditUserSummary

logger = logging.getLogger(__name__)


class ApproveRoleRequest:
    def __init__(
        self,
        db: Session,
        *,
        role_request: RoleRequest | str,
        approver_user: Optional[OktaUser | str] = None,
        approval_reason: str = "",
        ending_at: Optional[datetime] = None,
        notify: bool = True,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request
        
        self.role_request = (
            self.db.query(RoleRequest).options(
                joinedload(RoleRequest.active_requested_group), joinedload(RoleRequest.active_requester_role)
            )
            .filter(RoleRequest.id == (role_request if isinstance(role_request, str) else role_request.id))
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
        """Log audit event for role request approval."""
        # Get the group for audit logging
        group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.role_request.requested_group_id)
            .first()
        )

        requester = self.db.get(OktaUser, self.role_request.requester_user_id)

        # Build audit data
        audit_data = {
            "event_type": AuditEventType.ROLE_REQUEST_APPROVE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.approver_id,
            "current_user_email": self.approver_email,
            "group": AuditGroupSummary(
                id=group.id,
                name=group.name,
                type=group.type,
                app=AuditAppSummary(
                    id=group.app.id,
                    name=group.app.name
                ) if hasattr(group, 'app') and group.app else None
            ),
            "role_request": AuditRoleRequestSummary(
                id=self.role_request.id,
                requester_role=AuditRoleGroupSummary(
                    id=self.role_request.requester_role.id,
                    name=self.role_request.requester_role.name
                ) if self.role_request.requester_role else None,
                request_reason=self.role_request.request_reason,
                request_ending_at=self.role_request.request_ending_at,
                request_ownership=self.role_request.request_ownership,
                resolution_reason=self.approval_reason,
                approval_ending_at=self.ending_at
            ),
            "requester": AuditUserSummary(
                id=requester.id,
                email=requester.email,
                first_name=requester.first_name,
                last_name=requester.last_name,
                display_name=requester.display_name
            ) if requester else None,
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For") or
                self.request.headers.get("X-Real-IP") or
                self.request.client.host if self.request.client else None
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self) -> RoleRequest:
        # Don't allow approving a request that is already resolved
        if self.role_request.status != AccessRequestStatus.PENDING or self.role_request.resolved_at is not None:
            return self.role_request

        # Don't allow requester to approve their own request
        if self.role_request.requester_user_id == self.approver_id:
            return self.role_request

        # Don't allow approving a request if the reason is invalid and required
        valid, _ = CheckForReason(
            self.db,
            group=self.role_request.requester_role_id,
            reason=self.approval_reason,
            members_to_add=[self.role_request.requested_group_id] if not self.role_request.request_ownership else [],
            owners_to_add=[self.role_request.requested_group_id] if self.role_request.request_ownership else [],
        ).execute_for_role()
        if not valid:
            return self.role_request

        # Don't allow approving a request if the requester role is deleted
        requester = self.db.get(RoleGroup, self.role_request.requester_role_id)
        if requester is None or requester.deleted_at is not None:
            return self.role_request

        # Don't allow approving a request for an a deleted or unmanaged group
        if self.role_request.active_requested_group is None:
            return self.role_request
        if not self.role_request.active_requested_group.is_managed:
            return self.role_request

        self.db.commit()

        # Audit logging
        self._log_audit_event()

        if self.role_request.request_ownership:
            ModifyRoleGroups(
                self.db,
                role_group=self.role_request.requester_role,
                groups_added_ended_at=self.ending_at,
                owner_groups_to_add=[self.role_request.requested_group_id],
                current_user_id=self.approver_id,
                created_reason=self.approval_reason,
                notify=self.notify,
                request=self.request,
            ).execute()
        else:
            ModifyRoleGroups(
                self.db,
                role_group=self.role_request.requester_role,
                groups_added_ended_at=self.ending_at,
                groups_to_add=[self.role_request.requested_group_id],
                current_user_id=self.approver_id,
                created_reason=self.approval_reason,
                notify=self.notify,
                request=self.request,
            ).execute()

        return self.role_request