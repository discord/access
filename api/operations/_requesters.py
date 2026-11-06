"""Load the user or service principal that files a request."""

from __future__ import annotations

from sqlalchemy import select

from api.extensions import db
from api.models import OktaUser, ServicePrincipal


async def load_active_requester(requester_id: str | None) -> OktaUser | ServicePrincipal | None:
    """Return the active user or service principal with id `requester_id`, or None."""
    if requester_id is None:
        return None
    if ServicePrincipal.is_service_principal_id(requester_id):
        return (
            await db.session.scalars(
                select(ServicePrincipal)
                .where(ServicePrincipal.id == requester_id)
                .where(ServicePrincipal.deleted_at.is_(None))
            )
        ).first()
    return (
        await db.session.scalars(
            select(OktaUser).where(OktaUser.id == requester_id).where(OktaUser.deleted_at.is_(None))
        )
    ).first()


def split_requester(
    requester: OktaUser | ServicePrincipal | None,
) -> tuple[OktaUser | None, ServicePrincipal | None]:
    """Split a requester into its (user, service principal) pair, exactly one of which is set."""
    if isinstance(requester, ServicePrincipal):
        return None, requester
    return requester, None
