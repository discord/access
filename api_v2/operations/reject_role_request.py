import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import nullsfirst, func
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic

from api_v2.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleRequest
from api_v2.models.access_request import get_all_possible_request_approvers
from api_v2.plugins import get_notification_hook
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary, AuditRoleRequestSummary, AuditRoleGroupSummary, AuditUserSummary

logger = logging.getLogger(__name__)


class RejectRoleRequest:
    def __init__(
        self,
        db: Session,
        *,
        role_request: RoleRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: Optional[str | OktaUser] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request
        
        if isinstance(role_request, str):
            self.role_request = self.db.get(RoleRequest, role_request)
        else:
            self.role_request = role_request

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            self.rejecter_id = getattr(
                self.db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
                "id",
                None,
            )
        else:
            self.rejecter_id = current_user_id.id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def _log_audit_event(self) -> None:
        """Log audit event for role request rejection."""
        email = None
        if self.rejecter_id is not None:
            email = getattr(self.db.get(OktaUser, self.rejecter_id), "email", None)

        group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.id == self.role_request.requested_group_id)
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
            .first()
        )

        requester = self.db.get(OktaUser, self.role_request.requester_user_id)

        # Build audit data
        audit_data = {
            "event_type": AuditEventType.ROLE_REQUEST_REJECT,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.rejecter_id,
            "current_user_email": email,
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
                ) if hasattr(self.role_request, 'requester_role') and self.role_request.requester_role else None,
                request_reason=self.role_request.request_reason,
                request_ending_at=self.role_request.request_ending_at,
                request_ownership=self.role_request.request_ownership,
                resolution_reason=self.role_request.resolution_reason
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

        self.role_request.status = AccessRequestStatus.REJECTED
        self.role_request.resolved_at = func.now()
        self.role_request.resolver_user_id = self.rejecter_id
        self.role_request.resolution_reason = self.rejection_reason

        self.db.commit()

        # Audit logging
        self._log_audit_event()

        if self.notify:
            requester = self.db.get(OktaUser, self.role_request.requester_user_id)
            requester_role = self.db.get(OktaGroup, self.role_request.requester_role_id)

            approvers = get_all_possible_request_approvers(self.db, self.role_request)

            group = (
                self.db.query(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
                .filter(OktaGroup.id == self.role_request.requested_group_id)
                .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
                .first()
            )

            self.notification_hook.access_role_request_completed(
                role_request=self.role_request,
                role=requester_role,
                group=group,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return self.role_request