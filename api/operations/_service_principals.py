"""Shared helpers for the service principal operations."""

from __future__ import annotations

import logging
import secrets
import string
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.context import get_request_context
from api.exceptions import ConflictError
from api.extensions import db
from api.models import OktaUser, ServicePrincipal, ServicePrincipalOwner, ServicePrincipalProvider
from api.schemas import AuditLogSchema, EventType

_ID_ALPHABET = string.ascii_letters + string.digits
_ID_RANDOM_LENGTH = 17


def generate_service_principal_id() -> str:
    """Return a new service principal id: `ServicePrincipal.ID_PREFIX` plus random characters."""
    return ServicePrincipal.ID_PREFIX + "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_RANDOM_LENGTH))


async def load_active_service_principal(service_principal_id: str) -> ServicePrincipal | None:
    """Load an active service principal with its owner rows."""
    return (
        await db.session.scalars(
            select(ServicePrincipal)
            .options(selectinload(ServicePrincipal.owners))
            .where(ServicePrincipal.id == service_principal_id)
            .where(ServicePrincipal.deleted_at.is_(None))
        )
    ).first()


async def ensure_subject_available(
    provider: ServicePrincipalProvider, subject: str, *, exclude_id: str | None = None
) -> None:
    """Raise `ConflictError` if an active service principal already uses `(provider, subject)`."""
    stmt = (
        select(ServicePrincipal.id)
        .where(ServicePrincipal.provider == provider)
        .where(ServicePrincipal.subject == subject)
        .where(ServicePrincipal.deleted_at.is_(None))
    )
    if exclude_id is not None:
        stmt = stmt.where(ServicePrincipal.id != exclude_id)
    if (await db.session.scalars(stmt)).first() is not None:
        raise ConflictError("A service principal with this provider and subject already exists")


def owner_rows(user_ids: list[str], role_ids: list[str]) -> list[ServicePrincipalOwner]:
    """Build `ServicePrincipalOwner` rows for the given users and roles."""
    return [ServicePrincipalOwner(user_id=u) for u in user_ids] + [
        ServicePrincipalOwner(role_group_id=r) for r in role_ids
    ]


def audit_snapshot(service_principal: ServicePrincipal) -> dict[str, Any]:
    """Project a service principal (with owners loaded) into its audit-log shape."""
    return {
        "id": service_principal.id,
        "display_name": service_principal.display_name,
        "provider": service_principal.provider,
        "subject": service_principal.subject,
        "owner_user_ids": [o.user_id for o in service_principal.owners if o.user_id is not None],
        "owner_role_ids": [o.role_group_id for o in service_principal.owners if o.role_group_id is not None],
    }


def audit_ref(service_principal: ServicePrincipal | None) -> dict[str, Any] | None:
    """Identify a service principal acting as a requester in an audit-log entry."""
    if service_principal is None:
        return None
    return {"id": service_principal.id, "display_name": service_principal.display_name}


async def emit_audit_log(
    event_type: EventType,
    current_user_id: str | None,
    service_principal: dict[str, Any],
    old_service_principal: dict[str, Any] | None = None,
) -> None:
    """Emit the audit log entry for a service principal change."""
    actor = (
        (
            await db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
            )
        ).first()
        if current_user_id is not None
        else None
    )
    _ctx = get_request_context()
    payload: dict[str, Any] = {
        "event_type": event_type,
        "user_agent": _ctx.user_agent if _ctx else None,
        "ip": _ctx.ip if _ctx else None,
        "current_user_id": actor.id if actor else None,
        "current_user_email": actor.email if actor else None,
        "service_principal": service_principal,
    }
    if old_service_principal is not None:
        payload["old_service_principal"] = old_service_principal
    logging.getLogger("access.audit").info(AuditLogSchema().dumps(payload))
