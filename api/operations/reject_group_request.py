from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy import nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, GroupRequest
from api.models.access_request import get_all_possible_request_approvers
from api.plugins import get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class RejectGroupRequest:
    def __init__(
        self,
        *,
        group_request: GroupRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: Optional[str | OktaUser] = None,
    ):
        if isinstance(group_request, str):
            self.group_request = db.session.get(GroupRequest, group_request)
        else:
            self.group_request = group_request

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            user = OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first()
            self.rejecter_id = user.id if user else None
        else:
            self.rejecter_id = current_user_id.id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def execute(self) -> GroupRequest:
        # Don't allow rejecting a request that is already resolved
        if self.group_request.status != AccessRequestStatus.PENDING or self.group_request.resolved_at is not None:
            return self.group_request

        self.group_request.status = AccessRequestStatus.REJECTED
        self.group_request.resolved_at = db.func.now()
        self.group_request.resolver_user_id = self.rejecter_id
        self.group_request.resolution_reason = self.rejection_reason

        db.session.commit()

        # Audit logging
        email = None
        if self.rejecter_id is not None:
            email = getattr(db.session.get(OktaUser, self.rejecter_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_request_reject,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.rejecter_id,
                    "current_user_email": email,
                    "group_request": self.group_request,
                    "requester": db.session.get(OktaUser, self.group_request.requester_user_id),
                }
            )
        )

        if self.notify:
            requester = db.session.get(OktaUser, self.group_request.requester_user_id)

            approvers = get_all_possible_request_approvers(self.group_request)

            self.notification_hook.access_group_request_completed(
                group_request=self.group_request,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return self.group_request
