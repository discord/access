import functools
from typing import Optional

import logging

import anyio.to_thread

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
        self._role_request_arg = role_request
        self._current_user_id_arg = current_user_id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    async def _resolve(self) -> None:
        role_request = self._role_request_arg
        current_user_id = self._current_user_id_arg

        # Lock the request row so a reject can't race a concurrent approve/
        # reject; both serialize on this row and the loser hits the resolved
        # guard. No-op on SQLite.
        request_id = role_request if isinstance(role_request, str) else role_request.id
        request_result = await db.session.scalars(
            select(RoleRequest).where(RoleRequest.id == request_id).with_for_update()
        )
        self.role_request = request_result.first()

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            self.rejecter_id = getattr(
                (
                    await db.session.scalars(
                        select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
                    )
                ).first(),
                "id",
                None,
            )
        else:
            self.rejecter_id = current_user_id.id

    async def execute(self) -> RoleRequest:
        await self._resolve()
        # Don't allow rejecting a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent rejection surfaces
        # as a conflict instead of looking like a success.
        if self.role_request.status != AccessRequestStatus.PENDING or self.role_request.resolved_at is not None:
            raise ConflictError("Role request is no longer pending")

        self.role_request.status = AccessRequestStatus.REJECTED
        self.role_request.resolved_at = func.now()
        self.role_request.resolver_user_id = self.rejecter_id
        self.role_request.resolution_reason = self.rejection_reason

        await db.session.commit()

        # Audit logging
        email = None
        if self.rejecter_id is not None:
            email = getattr(await db.session.get(OktaUser, self.rejecter_id), "email", None)

        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.id == self.role_request.requested_group_id)
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
                    "current_user_id": self.rejecter_id,
                    "current_user_email": email,
                    "group": group,
                    "role_request": self.role_request,
                    "requester": await db.session.get(OktaUser, self.role_request.requester_user_id),
                }
            )
        )

        if self.notify:
            requester = await db.session.get(OktaUser, self.role_request.requester_user_id)
            requester_role = await db.session.get(OktaGroup, self.role_request.requester_role_id)

            approvers = await get_all_possible_request_approvers(self.role_request)

            # Sync notification hook may do network I/O; run it on a worker
            # thread so it doesn't block the event loop.
            await anyio.to_thread.run_sync(
                functools.partial(
                    self.notification_hook.access_role_request_completed,
                    role_request=self.role_request,
                    role=requester_role,
                    group=group,
                    requester=requester,
                    approvers=approvers,
                    notify_requester=self.notify_requester,
                )
            )

        return self.role_request
