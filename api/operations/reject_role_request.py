from typing import Optional

import logging

from api.context import get_request_context
from sqlalchemy import func, nullsfirst, select
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.exceptions import ConflictError
from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleRequest
from api.models.access_request import get_all_possible_request_approvers
from api.plugins import get_notification_hook
from api.schemas import AuditLogSchema, EventType


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
        self.role_request_id = role_request if isinstance(role_request, str) else role_request.id
        self.current_user_id = (
            current_user_id.id
            if current_user_id is not None and not isinstance(current_user_id, str)
            else current_user_id
        )

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    async def execute(self) -> RoleRequest:
        # Lock the request row so a reject can't race a concurrent approve/
        # reject; both serialize on this row and the loser hits the resolved
        # guard. No-op on SQLite.
        role_request = (
            await db.session.scalars(
                # `requester_role` is read by the audit-log serializer below and is
                # `lazy="raise_on_sql"`, so eager-load it here — a cold session (e.g.
                # the `access sync` cronjob deleting a user with pending role
                # requests) has no resident role group to satisfy it otherwise.
                # `of=RoleRequest` keeps FOR UPDATE off the joinedload's nullable
                # outer-join side (Postgres rejects that); no-op on SQLite.
                select(RoleRequest)
                .options(joinedload(RoleRequest.requester_role))
                .where(RoleRequest.id == self.role_request_id)
                .with_for_update(of=RoleRequest)
            )
        ).first()

        if self.current_user_id is None:
            rejecter_id = None
        else:
            rejecter_id = getattr(
                (
                    await db.session.scalars(
                        select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                    )
                ).first(),
                "id",
                None,
            )

        # Don't allow rejecting a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent rejection surfaces
        # as a conflict instead of looking like a success.
        if role_request.status != AccessRequestStatus.PENDING or role_request.resolved_at is not None:
            raise ConflictError("Role request is no longer pending")

        role_request.status = AccessRequestStatus.REJECTED
        role_request.resolved_at = func.now()
        role_request.resolver_user_id = rejecter_id
        role_request.resolution_reason = self.rejection_reason

        await db.session.commit()

        # Audit logging
        email = None
        if rejecter_id is not None:
            email = getattr(await db.session.get(OktaUser, rejecter_id), "email", None)

        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.id == role_request.requested_group_id)
                .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
            )
        ).first()

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema(exclude=["request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.role_request_reject,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": rejecter_id,
                    "current_user_email": email,
                    "group": group,
                    "role_request": role_request,
                    "requester": await db.session.get(OktaUser, role_request.requester_user_id),
                }
            )
        )

        if self.notify:
            requester = await db.session.get(OktaUser, role_request.requester_user_id)
            requester_role = await db.session.get(OktaGroup, role_request.requester_role_id)

            approvers = await get_all_possible_request_approvers(role_request)

            self.notification_hook.access_role_request_completed(
                role_request=role_request,
                role=requester_role,
                group=group,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return role_request
