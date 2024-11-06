from datetime import datetime
from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.operations.constraints import CheckForReason
from api.operations.modify_role_groups import ModifyRoleGroups
from api.plugins import get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class ApproveRoleRequest:
    def __init__(
        self,
        *,
        role_request: RoleRequest | str,
        approver_user: Optional[OktaUser | str] = None,
        approval_reason: str = "",
        ending_at: Optional[datetime] = None,
        notify: bool = True,
    ):
        self.role_request = (
            RoleRequest.query.options(
                joinedload(RoleRequest.active_requested_group), joinedload(RoleRequest.active_requester_role)
            )
            .filter(RoleRequest.id == (role_request if isinstance(role_request, str) else role_request.id))
            .first()
        )

        if approver_user is None:
            self.approver_id = None
            self.approver_email = None
        elif isinstance(approver_user, str):
            approver = db.session.get(OktaUser, approver_user)
            self.approver_id = approver.id
            self.approver_email = approver.email
        else:
            self.approver_id = approver_user.id
            self.approver_email = approver_user.email

        self.approval_reason = approval_reason

        self.ending_at = ending_at

        self.notify = notify

        self.notification_hook = get_notification_hook()

    def execute(self) -> RoleRequest:
        # Don't allow approving a request that is already resolved
        if self.role_request.status != AccessRequestStatus.PENDING or self.role_request.resolved_at is not None:
            return self.role_request

        # Don't allow requester to approve their own request
        if self.role_request.requester_user_id == self.approver_id:
            return self.role_request

        # Don't allow approving a request if the reason is invalid and required
        valid, _ = CheckForReason(
            group=self.role_request.requester_role_id,
            reason=self.approval_reason,
            members_to_add=[self.role_request.requested_group_id] if not self.role_request.request_ownership else [],
            owners_to_add=[self.role_request.requested_group_id] if self.role_request.request_ownership else [],
        ).execute_for_role()
        if not valid:
            return self.role_request

        # Don't allow approving a request if the requester role is deleted
        requester = db.session.get(RoleGroup, self.role_request.requester_role_id)
        if requester is None or requester.deleted_at is not None:
            return self.role_request

        # Don't allow approving a request for an a deleted or unmanaged group
        if self.role_request.active_requested_group is None:
            return self.role_request
        if not self.role_request.active_requested_group.is_managed:
            return self.role_request

        db.session.commit()

        # Audit logging
        group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.role_request.requested_group_id)
            .first()
        )

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.role_request_approve,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.approver_id,
                    "current_user_email": self.approver_email,
                    "group": group,
                    "role_request": self.role_request,
                    "requester": db.session.get(OktaUser, self.role_request.requester_user_id),
                }
            )
        )

        if self.role_request.request_ownership:
            ModifyRoleGroups(
                role_group=self.role_request.requester_role,
                groups_added_ended_at=self.ending_at,
                owner_groups_to_add=[self.role_request.requested_group_id],
                current_user_id=self.approver_id,
                created_reason=self.approval_reason,
                notify=self.notify,
            ).execute()
        else:
            ModifyRoleGroups(
                role_group=self.role_request.requester_role,
                groups_added_ended_at=self.ending_at,
                groups_to_add=[self.role_request.requested_group_id],
                current_user_id=self.approver_id,
                created_reason=self.approval_reason,
                notify=self.notify,
            ).execute()

        return self.role_request
