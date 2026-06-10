from typing import Optional

import logging

from api.context import get_request_context
from fastapi import HTTPException
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.models.access_request import get_all_possible_request_approvers
from api.plugins import get_notification_hook
from api.schemas import AuditLogSchema, EventType


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
        # Lock the request row so a reject can't race a concurrent approve/
        # reject; both serialize on this row and the loser hits the resolved
        # guard. No-op on SQLite.
        request_id = access_request if isinstance(access_request, str) else access_request.id
        self.access_request = (
            db.session.query(AccessRequest).filter(AccessRequest.id == request_id).with_for_update().first()
        )

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            self.rejecter_id = getattr(
                db.session.query(OktaUser)
                .filter(OktaUser.deleted_at.is_(None))
                .filter(OktaUser.id == current_user_id)
                .first(),
                "id",
                None,
            )
        else:
            self.rejecter_id = current_user_id.id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def execute(self) -> AccessRequest:
        # Don't allow rejecting a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent rejection surfaces
        # as a conflict instead of looking like a success.
        if self.access_request.status != AccessRequestStatus.PENDING or self.access_request.resolved_at is not None:
            raise HTTPException(409, "Access request is no longer pending")

        self.access_request.status = AccessRequestStatus.REJECTED
        self.access_request.resolved_at = func.now()
        self.access_request.resolver_user_id = self.rejecter_id
        self.access_request.resolution_reason = self.rejection_reason

        db.session.commit()

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

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema(exclude=["request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.access_reject,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
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
