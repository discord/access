from api.exceptions import InvalidRequestError
from api.extensions import db
from api.models import ServicePrincipal, ServicePrincipalProvider
from api.operations._active_ids import require_active_role_ids, require_active_user_ids
from api.operations._service_principals import (
    audit_snapshot,
    emit_audit_log,
    ensure_subject_available,
    generate_service_principal_id,
    load_active_service_principal,
    owner_rows,
)
from api.schemas import EventType


class CreateServicePrincipal:
    """Register a service principal.

    Args:
        display_name: Name shown wherever the service principal appears.
        provider: The identity provider that authenticates it.
        subject: The provider's identifier for it.
        owner_user_ids: Users accountable for it.
        owner_role_ids: Roles accountable for it.
        current_user_id: The acting user, for the audit log.

    Raises:
        InvalidRequestError: No owners were named, or an owner id is unknown.
        ConflictError: An active service principal already uses the provider and subject.
    """

    def __init__(
        self,
        *,
        display_name: str,
        provider: ServicePrincipalProvider,
        subject: str,
        owner_user_ids: list[str],
        owner_role_ids: list[str],
        current_user_id: str | None = None,
    ):
        self.display_name = display_name
        self.provider = provider
        self.subject = subject
        self.owner_user_ids = owner_user_ids
        self.owner_role_ids = owner_role_ids
        self.current_user_id = current_user_id

    async def execute(self) -> ServicePrincipal:
        """Create the service principal and return it with its owners loaded."""
        user_ids = await require_active_user_ids(self.owner_user_ids, "owner")
        role_ids = await require_active_role_ids(self.owner_role_ids, "owner")
        if not user_ids and not role_ids:
            raise InvalidRequestError("At least one owner is required")
        await ensure_subject_available(self.provider, self.subject)

        service_principal = ServicePrincipal(
            id=generate_service_principal_id(),
            display_name=self.display_name,
            provider=self.provider,
            subject=self.subject,
            owners=owner_rows(user_ids, role_ids),
        )
        db.session.add(service_principal)
        await db.session.commit()

        created = await load_active_service_principal(service_principal.id)
        assert created is not None
        await emit_audit_log(EventType.service_principal_create, self.current_user_id, audit_snapshot(created))
        return created
