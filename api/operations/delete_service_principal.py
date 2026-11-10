from sqlalchemy import func

from api.exceptions import ResourceGoneError
from api.extensions import db
from api.models import ServicePrincipal
from api.operations._service_principals import audit_snapshot, emit_audit_log, load_active_service_principal
from api.schemas import EventType


class DeleteServicePrincipal:
    """Soft-delete a service principal, so its credential no longer authenticates as it.

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
