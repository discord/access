from datetime import datetime
from typing import Optional

import logging

from api.context import get_request_context
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.exceptions import ConflictError, InvalidRequestError, ResourceGoneError

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.operations.constraints import CheckForReason
from api.operations.modify_role_groups import ModifyRoleGroups
from api.plugins import get_notification_hook
from api.schemas import AuditLogSchema, EventType


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
        # Lock the request row for the transaction so concurrent approvers
        # can't both pass the pending-state guard and double-grant. `of=` keeps
        # FOR UPDATE off the joinedloads' nullable outer-join sides (Postgres
        # rejects that); no-op on SQLite.
        self.role_request = (
            db.session.query(RoleRequest)
            .options(joinedload(RoleRequest.active_requested_group), joinedload(RoleRequest.active_requester_role))
            .filter(RoleRequest.id == (role_request if isinstance(role_request, str) else role_request.id))
            .with_for_update(of=RoleRequest)
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
        # Don't allow approving a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent approval surfaces
        # as a conflict instead of looking like a success.
        if self.role_request.status != AccessRequestStatus.PENDING or self.role_request.resolved_at is not None:
            raise ConflictError("Role request is no longer pending")

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
            raise ResourceGoneError("The requester role no longer exists")

        # Don't allow approving a request for a deleted or unmanaged group
        if self.role_request.active_requested_group is None:
            raise ResourceGoneError("The requested group no longer exists")
        if not self.role_request.active_requested_group.is_managed:
            raise InvalidRequestError("Groups not managed by Access cannot be modified")

        db.session.commit()

        # Audit logging
        group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.role_request.requested_group_id)
            .first()
        )

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.role_request_approve,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
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
