from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy import nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.models.access_request import get_all_possible_request_approvers
from api.plugins import get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class RejectRoleRequest:
    def __init__(
        self,
        *,
        role_request: RoleRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: Optional[str | OktaUser] = None,
    ):
        if isinstance(role_request, str):
            self.role_request = db.session.get(RoleRequest, role_request)
        else:
            self.role_request = role_request

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            self.rejecter_id = getattr(
                OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
                "id",
                None,
            )
        else:
            self.rejecter_id = current_user_id.id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def execute(self) -> RoleRequest:
        # Don't allow approving a request that is already resolved
        if self.role_request.status != AccessRequestStatus.PENDING or self.role_request.resolved_at is not None:
            return self.role_request

        self.role_request.status = AccessRequestStatus.REJECTED
        self.role_request.resolved_at = db.func.now()
        self.role_request.resolver_user_id = self.rejecter_id
        self.role_request.resolution_reason = self.rejection_reason

        db.session.commit()

        # Audit logging
        email = None
        if self.rejecter_id is not None:
            email = getattr(db.session.get(OktaUser, self.rejecter_id), "email", None)

        group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.id == self.role_request.requested_group_id)
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
            .first()
        )

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema(exclude=["request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.role_request_reject,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.rejecter_id,
                    "current_user_email": email,
                    "group": group,
                    "role_request": self.role_request, # TODO might need to change to separate out requester role
                    "requester": db.session.get(OktaUser, self.role_request.requester_user_id),
                }
            )
        )

        if self.notify:
            requester = db.session.get(OktaUser, self.role_request.requester_user_id)
            requester_role = db.session.get(OktaGroup, self.role_request.requester_role)

            approvers = get_all_possible_request_approvers(self.role_request)

            self.notification_hook.access_role_request_completed(
                role_request=self.role_request,
                role = requester_role,
                group=group,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return self.role_request
