from sqlalchemy import func, select

from api.exceptions import ResourceGoneError
from api.extensions import db
from api.models import AccessRequestStatus, AppRequest, DeletionRequest, GroupRequest, ServicePrincipal
from api.operations._service_principals import audit_snapshot, emit_audit_log, load_active_service_principal
from api.operations.reject_app_request import RejectAppRequest
from api.operations.reject_deletion_request import RejectDeletionRequest
from api.operations.reject_group_request import RejectGroupRequest
from api.schemas import EventType


class DeleteServicePrincipal:
    """Soft-delete a service principal, so its credential no longer authenticates, and
    reject its pending requests.

    Args:
        service_principal: The service principal or its id.
        current_user_id: The acting user, for the audit log.

    Raises:
        ResourceGoneError: The service principal does not exist or was already deleted.
    """

    def __init__(self, *, service_principal: ServicePrincipal | str, current_user_id: str | None = None):
        self.service_principal_id = service_principal if isinstance(service_principal, str) else service_principal.id
        self.current_user_id = current_user_id

    async def execute(self) -> None:
        """Mark the service principal deleted."""
        service_principal = await load_active_service_principal(self.service_principal_id)
        if service_principal is None:
            raise ResourceGoneError("Service principal not found")
        snapshot = audit_snapshot(service_principal)
        service_principal.deleted_at = func.now()
        await db.session.commit()
        await emit_audit_log(EventType.service_principal_delete, self.current_user_id, snapshot)

        # Its pending requests could never be approved now, so close them.
        reason = "Closed because the requesting service principal was deleted"
        for group_request in (
            await db.session.scalars(
                select(GroupRequest)
                .where(GroupRequest.requester_service_principal_id == self.service_principal_id)
                .where(GroupRequest.status == AccessRequestStatus.PENDING)
                .where(GroupRequest.resolved_at.is_(None))
            )
        ).all():
            await RejectGroupRequest(
                group_request=group_request, rejection_reason=reason, current_user_id=self.current_user_id
            ).execute()
        for app_request in (
            await db.session.scalars(
                select(AppRequest)
                .where(AppRequest.requester_service_principal_id == self.service_principal_id)
                .where(AppRequest.status == AccessRequestStatus.PENDING)
                .where(AppRequest.resolved_at.is_(None))
            )
        ).all():
            await RejectAppRequest(
                app_request=app_request, rejection_reason=reason, current_user_id=self.current_user_id
            ).execute()
        for deletion_request in (
            await db.session.scalars(
                select(DeletionRequest)
                .where(DeletionRequest.requester_service_principal_id == self.service_principal_id)
                .where(DeletionRequest.status == AccessRequestStatus.PENDING)
                .where(DeletionRequest.resolved_at.is_(None))
            )
        ).all():
            await RejectDeletionRequest(
                deletion_request=deletion_request, rejection_reason=reason, current_user_id=self.current_user_id
            ).execute()
