import logging

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, with_polymorphic

from api.context import get_request_context
from api.exceptions import ConflictError
from api.extensions import db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    DeletionRequest,
    OktaGroup,
    OktaUser,
    RoleGroup,
    ServicePrincipal,
)
from api.operations._deletion_requests import approvers_for, can_approve, lock_pending
from api.operations._fan_out import defer_notification
from api.operations._service_principals import audit_ref
from api.plugins import NotificationHook
from api.schemas import AuditLogSchema, EventType


class RejectDeletionRequest:
    """Reject a pending deletion request.

    Anyone who could approve it may reject it, and its requester (user or service
    principal) may withdraw it. With no rejecter, the system or a
    conditional-access plugin is rejecting it.

    Args:
        deletion_request: The request or its id.
        rejection_reason: Recorded as the resolution reason.
        notify: Whether to send notifications.
        notify_requester: Whether the notification goes to the requester.
        current_user_id: The rejecting user or service principal, or None.

    Raises:
        ConflictError: The request is no longer pending.
    """

    def __init__(
        self,
        *,
        deletion_request: DeletionRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: str | None = None,
    ):
        self.deletion_request_id = deletion_request if isinstance(deletion_request, str) else deletion_request.id
        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester
        self.current_user_id = current_user_id

    async def execute(self) -> DeletionRequest:
        """Reject the request and return it, or return it unchanged when the caller may not reject it."""
        deletion_request = await lock_pending(self.deletion_request_id)
        assert deletion_request is not None
        if deletion_request.status != AccessRequestStatus.PENDING or deletion_request.resolved_at is not None:
            raise ConflictError("Deletion request is no longer pending")

        # The target may have been deleted since; a rejection still reports it.
        group: OktaGroup | None = None
        app: App | None = None
        if deletion_request.group_id is not None:
            poly_group = with_polymorphic(OktaGroup, [AppGroup, RoleGroup])
            group = (
                await db.session.scalars(
                    select(poly_group)
                    .options(joinedload(poly_group.AppGroup.app))
                    .where(poly_group.id == deletion_request.group_id)
                )
            ).first()
        else:
            app = await db.session.get(App, deletion_request.app_id)

        rejecter: OktaUser | None = None
        is_requester = self.current_user_id in (
            deletion_request.requester_user_id,
            deletion_request.requester_service_principal_id,
        )
        if self.current_user_id is not None:
            if not is_requester:
                rejecter = (
                    await db.session.scalars(
                        select(OktaUser).where(OktaUser.id == self.current_user_id).where(OktaUser.deleted_at.is_(None))
                    )
                ).first()
                if rejecter is None or not await can_approve(rejecter.id, group, app):
                    return deletion_request
            elif not ServicePrincipal.is_service_principal_id(self.current_user_id):
                rejecter = await db.session.get(OktaUser, self.current_user_id)

        requester_user = (
            await db.session.get(OktaUser, deletion_request.requester_user_id)
            if deletion_request.requester_user_id
            else None
        )
        requester_service_principal = (
            await db.session.get(ServicePrincipal, deletion_request.requester_service_principal_id)
            if deletion_request.requester_service_principal_id
            else None
        )

        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.deletion_request_reject,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": rejecter.email if rejecter else None,
                    "deletion_request": deletion_request,
                    "group": group,
                    "app": app,
                    "requester": requester_user,
                    "service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        deletion_request.status = AccessRequestStatus.REJECTED
        deletion_request.resolved_at = func.now()
        # `resolver_user_id` names a user; a service principal withdrawing its own
        # request leaves it empty, and the audit log records who acted.
        deletion_request.resolver_user_id = rejecter.id if rejecter else None
        deletion_request.resolution_reason = self.rejection_reason
        await db.session.commit()

        # A service principal learns the outcome by polling the request, so only a
        # user requester is notified.
        if self.notify and requester_user is not None:
            approvers = await approvers_for(group, app)
            await defer_notification(
                db.session,
                NotificationHook.ACCESS_DELETION_REQUEST_COMPLETED,
                detach=[deletion_request, group, app, requester_user, *approvers],
                deletion_request=deletion_request,
                group=group,
                app=app,
                requester=requester_user,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )
        return deletion_request
