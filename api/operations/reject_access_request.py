from typing import Optional

import logging

from api.context import get_request_context
from sqlalchemy import func, nullsfirst, select
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.exceptions import ConflictError
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
        self._access_request_arg = access_request
        self._current_user_id_arg = current_user_id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def execute(self) -> AccessRequest:
        access_request_arg = self._access_request_arg
        current_user_id = self._current_user_id_arg

        # Lock the request row so a reject can't race a concurrent approve/
        # reject; both serialize on this row and the loser hits the resolved
        # guard. No-op on SQLite.
        request_id = access_request_arg if isinstance(access_request_arg, str) else access_request_arg.id
        access_request = db.session.scalars(
            select(AccessRequest).where(AccessRequest.id == request_id).with_for_update()
        ).first()

        if current_user_id is None:
            rejecter_id = None
        elif isinstance(current_user_id, str):
            rejecter_id = getattr(
                db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
                ).first(),
                "id",
                None,
            )
        else:
            rejecter_id = current_user_id.id

        # Don't allow rejecting a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent rejection surfaces
        # as a conflict instead of looking like a success.
        if access_request.status != AccessRequestStatus.PENDING or access_request.resolved_at is not None:
            raise ConflictError("Access request is no longer pending")

        access_request.status = AccessRequestStatus.REJECTED
        access_request.resolved_at = func.now()
        access_request.resolver_user_id = rejecter_id
        access_request.resolution_reason = self.rejection_reason

        db.session.commit()

        # Audit logging
        email = None
        if rejecter_id is not None:
            email = getattr(db.session.get(OktaUser, rejecter_id), "email", None)

        group = db.session.scalars(
            select(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .where(OktaGroup.id == access_request.requested_group_id)
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
        ).first()

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema(exclude=["request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.access_reject,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": rejecter_id,
                    "current_user_email": email,
                    "group": group,
                    "request": access_request,
                    "requester": db.session.get(OktaUser, access_request.requester_user_id),
                }
            )
        )

        if self.notify:
            requester = db.session.get(OktaUser, access_request.requester_user_id)

            approvers = get_all_possible_request_approvers(access_request)

            self.notification_hook.access_request_completed(
                access_request=access_request,
                group=group,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return access_request
