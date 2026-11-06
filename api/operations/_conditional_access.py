"""Apply conditional-access plugin decisions to a newly created request."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import select

from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import AccessRequestStatus
from api.plugins import ConditionalAccessResponse

logger = logging.getLogger(__name__)


async def apply_conditional_access(
    responses: Sequence[ConditionalAccessResponse | None],
    *,
    request_label: str,
    approve: Callable[[ConditionalAccessResponse], Awaitable[object]],
    reject: Callable[[ConditionalAccessResponse], Awaitable[object]],
    status: Callable[[], Awaitable[AccessRequestStatus]],
) -> bool:
    """Act on the first plugin decision; return whether the request was resolved.

    An approval that can't be carried out (the request stays pending, for
    example because a name was taken since it was filed) is logged and leaves
    the request for its reviewers, as does having no decision at all.

    Args:
        responses: The plugins' responses, in order; None means no decision.
        request_label: Names the request in log messages, such as "group request abc".
        approve: Approves the request on behalf of a response.
        reject: Rejects the request on behalf of a response.
        status: Reads the request's current status from the database. Approval may
            expunge the session, so the caller's instance can't be trusted to be current.
    """
    for response in responses:
        if response is None:
            continue
        if not response.approved:
            await reject(response)
            return True
        try:
            await approve(response)
        except (ConflictError, InvalidRequestError) as e:
            logger.warning("Conditional access approval of %s failed: %s", request_label, e)
        return await status() != AccessRequestStatus.PENDING
    return False


async def current_status(model: Any, request_id: str) -> AccessRequestStatus:
    """Read a request's status from the database, bypassing any stale instance in the session."""
    status = (await db.session.scalars(select(model.status).where(model.id == request_id))).one()
    return status
