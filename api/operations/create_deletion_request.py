import logging
import random
import string

from api.context import get_request_context
from api.exceptions import InvalidRequestError
from api.extensions import db
from api.models import AccessRequestStatus, DeletionRequest, OktaUser, ServicePrincipal
from api.operations._conditional_access import apply_conditional_access, current_status
from api.operations._deletion_requests import approvers_for, load_deletable_target
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester, split_requester
from api.operations._service_principals import audit_ref
from api.operations.approve_deletion_request import ApproveDeletionRequest
from api.operations.reject_deletion_request import RejectDeletionRequest
from api.plugins import ConditionalAccessHook, NotificationHook, evaluate_conditional_access
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


class CreateDeletionRequest:
    """Create a request to delete a group or an app, then auto-approve, reject, or route it.

    It routes to whoever could delete the target directly: the group's owners,
    then its app's owners, then Access admins.

    Args:
        requester: The user or service principal filing the request, or its id.
        group_id: The group to delete. Exactly one of `group_id` and `app_id` is given.
        app_id: The app to delete.
        request_reason: Why the target should be deleted.

    Raises:
        ResourceGoneError: The target doesn't exist or was deleted.
        InvalidRequestError: The target can't be deleted.
    """

    def __init__(
        self,
        *,
        requester: OktaUser | ServicePrincipal | str,
        group_id: str | None = None,
        app_id: str | None = None,
        request_reason: str = "",
    ):
        if (group_id is None) == (app_id is None):
            raise InvalidRequestError("Name exactly one of group_id and app_id")
        self.id = "".join(random.choices(string.ascii_letters + string.digits, k=20))
        self.requester_id = requester if isinstance(requester, str) else requester.id
        self.group_id = group_id
        self.app_id = app_id
        self.request_reason = request_reason

    async def execute(self) -> DeletionRequest:
        """Create the request and return it."""
        requester = await load_active_requester(self.requester_id)
        assert requester is not None
        requester_user, requester_service_principal = split_requester(requester)
        group, app = await load_deletable_target(group_id=self.group_id, app_id=self.app_id)

        deletion_request = DeletionRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=requester_user.id if requester_user else None,
            requester_service_principal_id=requester_service_principal.id if requester_service_principal else None,
            group_id=group.id if group else None,
            app_id=app.id if app else None,
            request_reason=self.request_reason,
        )
        db.session.add(deletion_request)
        await db.session.commit()
        deletion_request_id = deletion_request.id

        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.deletion_request_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": requester.id,
                    "current_user_email": requester_user.email if requester_user else None,
                    "deletion_request": deletion_request,
                    "group": group,
                    "app": app,
                    "requester": requester_user,
                    "requester_service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        if group is not None:
            responses = await evaluate_conditional_access(
                ConditionalAccessHook.GROUP_DELETION_REQUEST_CREATED,
                deletion_request=deletion_request,
                group=group,
                requester=requester,
            )
        else:
            responses = await evaluate_conditional_access(
                ConditionalAccessHook.APP_DELETION_REQUEST_CREATED,
                deletion_request=deletion_request,
                app=app,
                requester=requester,
            )
        if await apply_conditional_access(
            responses,
            request_label=f"deletion request {deletion_request_id}",
            approve=lambda r: ApproveDeletionRequest(
                deletion_request=deletion_request_id, approval_reason=r.reason, notify=False
            ).execute(),
            reject=lambda r: RejectDeletionRequest(
                deletion_request=deletion_request_id, rejection_reason=r.reason, notify=False
            ).execute(),
            status=lambda: current_status(DeletionRequest, deletion_request_id),
        ):
            return deletion_request

        approvers = await approvers_for(group, app)
        await defer_notification(
            db.session,
            NotificationHook.ACCESS_DELETION_REQUEST_CREATED,
            detach=[deletion_request, group, app, requester, *approvers],
            deletion_request=deletion_request,
            group=group,
            app=app,
            requester=requester,
            approvers=approvers,
        )
        return deletion_request
