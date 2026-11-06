import logging

from sqlalchemy import func

from api.auth.permissions import can_approve_deletion
from api.context import get_request_context
from api.exceptions import ConflictError
from api.extensions import db
from api.models import AccessRequestStatus, DeletionRequest, OktaUser, ServicePrincipal
from api.operations._deletion_requests import approvers_for, load_deletable_target, lock_pending
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester
from api.operations._service_principals import audit_ref
from api.operations.delete_app import DeleteApp
from api.operations.delete_group import DeleteGroup
from api.plugins import NotificationHook
from api.schemas import AuditLogSchema, EventType


class ApproveDeletionRequest:
    """Approve a pending deletion request by deleting its target as a direct delete would.

    Args:
        deletion_request: The request or its id.
        approver_user: The approving user, or None for a conditional-access plugin approval.
            The approver must be able to delete the target directly and can't be the requester.
        approval_reason: Recorded as the resolution reason.
        notify: Whether to send notifications.

    Raises:
        ConflictError: The request is no longer pending.
        ResourceGoneError: The target was deleted since the request was filed.
        InvalidRequestError: The target can no longer be deleted.
    """

    def __init__(
        self,
        *,
        deletion_request: DeletionRequest | str,
        approver_user: OktaUser | str | None = None,
        approval_reason: str = "",
        notify: bool = True,
    ):
        self.deletion_request_id = deletion_request if isinstance(deletion_request, str) else deletion_request.id
        self.approver_user_id = approver_user if not isinstance(approver_user, OktaUser) else approver_user.id
        self.approval_reason = approval_reason
        self.notify = notify

    async def execute(self) -> DeletionRequest | None:
        """Approve the request and return it, or return it unchanged when the approver may not approve it."""
        deletion_request = await lock_pending(self.deletion_request_id)
        if deletion_request is None:
            return None
        if deletion_request.status != AccessRequestStatus.PENDING or deletion_request.resolved_at is not None:
            raise ConflictError("Deletion request is no longer pending")

        requester = await load_active_requester(
            deletion_request.requester_service_principal_id or deletion_request.requester_user_id
        )
        if requester is None:
            return deletion_request
        requester_user = requester if isinstance(requester, OktaUser) else None
        requester_service_principal = requester if isinstance(requester, ServicePrincipal) else None

        group, app = await load_deletable_target(group_id=deletion_request.group_id, app_id=deletion_request.app_id)

        approver: OktaUser | None = None
        if self.approver_user_id is not None:
            approver = await db.session.get(OktaUser, self.approver_user_id)
            if approver is None or approver.deleted_at is not None or approver.id == deletion_request.requester_user_id:
                return deletion_request
            if not await can_approve_deletion(db.session, approver.id, group=group, app=app):
                return deletion_request
        approver_id = approver.id if approver else None

        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.deletion_request_approve,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": approver_id,
                    "current_user_email": approver.email if approver else None,
                    "deletion_request": deletion_request,
                    "group": group,
                    "app": app,
                    "requester": requester_user,
                    "requester_service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        # Who to notify is read before the delete ends the target's ownerships.
        approvers = await approvers_for(group, app) if self.notify and requester_user is not None else []

        if group is not None:
            await DeleteGroup(
                group=group, current_user_id=approver_id, deletion_request_id=deletion_request.id
            ).execute()
        else:
            assert app is not None
            await DeleteApp(app=app, current_user_id=approver_id, deletion_request_id=deletion_request.id).execute()

        deletion_request = await db.session.get(DeletionRequest, self.deletion_request_id)
        assert deletion_request is not None
        deletion_request.status = AccessRequestStatus.APPROVED
        deletion_request.resolved_at = func.now()
        deletion_request.resolver_user_id = approver_id
        deletion_request.resolution_reason = self.approval_reason
        await db.session.commit()

        # A service principal learns the outcome by polling the request, so only a
        # user requester is notified.
        if self.notify and requester_user is not None:
            await defer_notification(
                db.session,
                NotificationHook.ACCESS_DELETION_REQUEST_COMPLETED,
                detach=[deletion_request, group, app, requester_user, *approvers],
                deletion_request=deletion_request,
                group=group,
                app=app,
                requester=requester_user,
                approvers=approvers,
                notify_requester=True,
            )
        return deletion_request
