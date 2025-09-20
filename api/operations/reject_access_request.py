from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy import nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.models.access_request import get_all_possible_request_approvers
from api.plugins import get_notification_hook
from api.plugins.metrics_reporter import get_metrics_reporter_hook
from api.views.schemas import AuditLogSchema, EventType


class RejectAccessRequest:
    def __init__(
        self,
        *,
        access_request: AccessRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: Optional[str | OktaUser] = None,
    ):
        if isinstance(access_request, str):
            self.access_request = db.session.get(AccessRequest, access_request)
        else:
            self.access_request = access_request

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
        self.metrics_hook = get_metrics_reporter_hook()

    def execute(self) -> AccessRequest:
        # Don't allow approving a request that is already resolved
        if self.access_request.status != AccessRequestStatus.PENDING or self.access_request.resolved_at is not None:
            return self.access_request

        # Calculate rejection time in seconds
        from datetime import datetime

        creation_time = self.access_request.created_at
        rejection_time = datetime.utcnow()
        resolution_time_seconds = (rejection_time - creation_time).total_seconds()

        self.access_request.status = AccessRequestStatus.REJECTED
        self.access_request.resolved_at = db.func.now()
        self.access_request.resolver_user_id = self.rejecter_id
        self.access_request.resolution_reason = self.rejection_reason

        db.session.commit()

        # Get group type for metrics
        requested_group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.id == self.access_request.requested_group_id)
            .first()
        )

        # Record metrics for access request rejection
        group_type = "app_group" if isinstance(requested_group, AppGroup) else "role_group"
        self.metrics_hook.record_counter(
            metric_name="access.request.rejected",
            value=1.0,
            tags={
                "group_type": group_type,
                "request_ownership": str(self.access_request.request_ownership).lower(),
            },
        )
        self.metrics_hook.record_histogram(
            metric_name="access.request.resolution_time",
            value=resolution_time_seconds,
            tags={
                "resolution_type": "rejected",
                "group_type": group_type,
            },
        )

        # Audit logging
        email = None
        if self.rejecter_id is not None:
            email = getattr(db.session.get(OktaUser, self.rejecter_id), "email", None)

        group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.id == self.access_request.requested_group_id)
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
            .first()
        )

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema(exclude=["request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.access_reject,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.rejecter_id,
                    "current_user_email": email,
                    "group": group,
                    "request": self.access_request,
                    "requester": db.session.get(OktaUser, self.access_request.requester_user_id),
                }
            )
        )

        if self.notify:
            requester = db.session.get(OktaUser, self.access_request.requester_user_id)

            approvers = get_all_possible_request_approvers(self.access_request)

            self.notification_hook.access_request_completed(
                access_request=self.access_request,
                group=group,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return self.access_request
