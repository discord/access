"""Carry out the grants an approved request names, one at a time."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from api.extensions import db

logger = logging.getLogger(__name__)


async def carry_out_grants(
    grants: Sequence[tuple[str, Callable[[], Awaitable[object]]]], request_label: str
) -> list[str]:
    """Run each grant, continuing past failures, and return the labels of those that failed.

    The group or app the grants are on already exists by now, so one failed grant
    (for example, a role deleted concurrently) must not leave the request pending
    with a name that can never be approved again. A failed grant is rolled back
    and logged; the ones before it stay committed.

    Args:
        grants: Pairs of a label naming the grant and a callable that makes it.
        request_label: Names the request in log messages, such as "group request abc".
    """
    failed: list[str] = []
    for label, grant in grants:
        try:
            await grant()
        except Exception:
            await db.session.rollback()
            logger.exception("Granting %s for %s failed", label, request_label)
            failed.append(label)
    return failed


def failed_grants_note(failed: list[str]) -> str:
    """A resolution-reason suffix naming the grants that failed, or "" when none did."""
    return f" (Not granted, see logs: {'; '.join(failed)})" if failed else ""
