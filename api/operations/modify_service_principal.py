from api.exceptions import InvalidRequestError, ResourceGoneError
from api.extensions import db
from api.models import ServicePrincipal
from api.operations._active_ids import require_active_role_ids, require_active_user_ids
from api.operations._service_principals import (
    audit_snapshot,
    commit_subject_change,
    emit_audit_log,
    ensure_subject_available,
    load_active_service_principal,
    owner_rows,
)
from api.schemas import EventType


class ModifyServicePrincipal:
    """Rename a service principal, change its subject, or replace its owners.

    Args:
        service_principal: The service principal or its id.
        display_name: New display name, or None to leave it unchanged.
        subject: New subject (for example after a token is replaced), or None to leave it unchanged.
        owner_user_ids: Replacement user owners, or None to leave them unchanged.
        owner_role_ids: Replacement role owners, or None to leave them unchanged.
        current_user_id: The acting user, for the audit log.

    Raises:
        ResourceGoneError: The service principal does not exist or was deleted.
        InvalidRequestError: The change would leave no owners, or an owner id is unknown.
        ConflictError: Another active service principal already uses the new subject.
    """

    def __init__(
        self,
        *,
        service_principal: ServicePrincipal | str,
        display_name: str | None = None,
        subject: str | None = None,
        owner_user_ids: list[str] | None = None,
        owner_role_ids: list[str] | None = None,
        current_user_id: str | None = None,
    ):
        self.service_principal_id = service_principal if isinstance(service_principal, str) else service_principal.id
        self.display_name = display_name
        self.subject = subject
        self.owner_user_ids = owner_user_ids
        self.owner_role_ids = owner_role_ids
        self.current_user_id = current_user_id

    async def execute(self) -> ServicePrincipal:
        """Apply the changes and return the service principal with its owners loaded."""
        service_principal = await load_active_service_principal(self.service_principal_id)
        if service_principal is None:
            raise ResourceGoneError("Service principal not found")
        old = audit_snapshot(service_principal)

        user_ids = (
            await require_active_user_ids(self.owner_user_ids, "owner")
            if self.owner_user_ids is not None
            else old["owner_user_ids"]
        )
        role_ids = (
            await require_active_role_ids(self.owner_role_ids, "owner")
            if self.owner_role_ids is not None
            else old["owner_role_ids"]
        )
        if not user_ids and not role_ids:
            raise InvalidRequestError("At least one owner is required")

        subject_changed = self.subject is not None and self.subject != service_principal.subject
        if subject_changed:
            await ensure_subject_available(service_principal.provider, self.subject, exclude_id=service_principal.id)
            service_principal.subject = self.subject
        if self.display_name is not None:
            service_principal.display_name = self.display_name
        if self.owner_user_ids is not None or self.owner_role_ids is not None:
            service_principal.owners = owner_rows(user_ids, role_ids)
        service_principal_id = service_principal.id
        await commit_subject_change(service_principal, subject_changed)

        db.session.expire_all()
        modified = await load_active_service_principal(service_principal_id)
        assert modified is not None
        await emit_audit_log(
            EventType.service_principal_modify,
            self.current_user_id,
            audit_snapshot(modified),
            old_service_principal=old,
        )
        return modified
