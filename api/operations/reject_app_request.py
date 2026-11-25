import logging

from sqlalchemy import func, select

from api.context import get_request_context
from api.exceptions import ConflictError
from api.extensions import db
from api.models import AccessRequestStatus, AppRequest, OktaUser, ServicePrincipal
from api.models.app_group import get_access_owners
from api.operations._fan_out import defer_notification
from api.operations._service_principals import audit_ref
from api.plugins import NotificationHook
from api.schemas import AuditLogSchema, EventType


class RejectAppRequest:
    """Reject a pending app request.

    An Access admin may reject any request; its requester (user or service
    principal) may withdraw its own. With no rejecter, the system or a
    conditional-access plugin is rejecting it.

    Args:
        app_request: The request or its id.
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
        app_request: AppRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: str | None = None,
    ):
        self.app_request_id = app_request if isinstance(app_request, str) else app_request.id
        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester
        self.current_user_id = current_user_id

    async def execute(self) -> AppRequest:
        """Reject the request and return it, or return it unchanged when the caller may not reject it."""
        app_request = (
            await db.session.scalars(select(AppRequest).where(AppRequest.id == self.app_request_id).with_for_update())
        ).first()
        assert app_request is not None
        if app_request.status != AccessRequestStatus.PENDING or app_request.resolved_at is not None:
            raise ConflictError("App request is no longer pending")

        rejecter: OktaUser | None = None
        if self.current_user_id is not None:
            if not app_request.is_requested_by(self.current_user_id):
                rejecter = (
                    await db.session.scalars(
                        select(OktaUser).where(OktaUser.id == self.current_user_id).where(OktaUser.deleted_at.is_(None))
                    )
                ).first()
                if rejecter is None or rejecter.id not in {u.id for u in await get_access_owners()}:
                    return app_request
            elif not ServicePrincipal.is_service_principal_id(self.current_user_id):
                rejecter = await db.session.get(OktaUser, self.current_user_id)

        requester_user = (
            await db.session.get(OktaUser, app_request.requester_user_id) if app_request.requester_user_id else None
        )
        requester_service_principal = (
            await db.session.get(ServicePrincipal, app_request.requester_service_principal_id)
            if app_request.requester_service_principal_id
            else None
        )

        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_request_reject,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": rejecter.email if rejecter else None,
                    "app_request": app_request,
                    "requester": requester_user,
                    "requester_service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        app_request.status = AccessRequestStatus.REJECTED
        app_request.resolved_at = func.now()
        app_request.resolver_user_id = rejecter.id if rejecter else None
        if ServicePrincipal.is_service_principal_id(self.current_user_id):
            app_request.resolver_service_principal_id = self.current_user_id
        app_request.resolution_reason = self.rejection_reason
        await db.session.commit()

        # A service principal learns the outcome by polling the request, so only a
        # user requester is notified.
        if self.notify and requester_user is not None:
            approvers = await get_access_owners()
            await defer_notification(
                db.session,
                NotificationHook.ACCESS_APP_REQUEST_COMPLETED,
                detach=[app_request, requester_user, *approvers],
                app_request=app_request,
                app=None,
                requester=requester_user,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return app_request
