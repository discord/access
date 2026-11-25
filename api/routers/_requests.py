"""Shared query pieces for the request routers (group, app, and deletion requests)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.orm import aliased

from api.database import DbSession
from api.models import AccessRequestStatus, OktaUser, ServicePrincipal


def filter_by_requester(stmt: Select[Any], model: Any, value: str, current_user_id: str) -> Select[Any]:
    """Keep requests filed by `value`: "@me", or a user's id or email, or a service principal's id or display name."""
    if value == "@me":
        return stmt.where(
            or_(model.requester_user_id == current_user_id, model.requester_service_principal_id == current_user_id)
        )
    user = aliased(OktaUser)
    service_principal = aliased(ServicePrincipal)
    return (
        stmt.outerjoin(model.requester.of_type(user))
        .outerjoin(model.requester_service_principal.of_type(service_principal))
        .where(
            or_(
                model.requester_user_id == value,
                user.email.ilike(value),
                model.requester_service_principal_id == value,
                service_principal.display_name.ilike(value),
            )
        )
    )


def join_requester_for_search(
    stmt: Select[Any], model: Any, like: str
) -> tuple[Select[Any], list[ColumnElement[bool]]]:
    """Join a request's requester and return the free-text clauses matching it against `like`."""
    user = aliased(OktaUser)
    service_principal = aliased(ServicePrincipal)
    stmt = stmt.outerjoin(model.requester.of_type(user)).outerjoin(
        model.requester_service_principal.of_type(service_principal)
    )
    return stmt, [
        user.email.ilike(like),
        user.first_name.ilike(like),
        user.last_name.ilike(like),
        user.display_name.ilike(like),
        (user.first_name + " " + user.last_name).ilike(like),
        service_principal.display_name.ilike(like),
    ]


async def withdraw_prior_requests(
    db: DbSession,
    model: Any,
    requester: OktaUser | ServicePrincipal,
    same_target: Sequence[ColumnElement[bool]],
    reject: Callable[[Any], Awaitable[object]],
) -> None:
    """Reject the requester's pending requests for the same target, so a retry replaces them.

    Args:
        db: The session.
        model: The request model.
        requester: Who is filing the new request.
        same_target: Conditions matching a request for the same target.
        reject: Rejects one prior request.
    """
    requester_column = (
        model.requester_service_principal_id if isinstance(requester, ServicePrincipal) else model.requester_user_id
    )
    priors = (
        await db.scalars(
            select(model)
            .where(requester_column == requester.id)
            .where(*same_target)
            .where(model.status == AccessRequestStatus.PENDING)
            .where(model.resolved_at.is_(None))
        )
    ).all()
    for prior in priors:
        await reject(prior)
