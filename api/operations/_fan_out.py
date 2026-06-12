"""Draining helper for the Okta/notification task fan-out in operations.

The fan-out operations (`ModifyGroupUsers`, `ModifyRoleGroups`, `DeleteUser`,
`DeleteGroup`) spawn Okta API calls and notification dispatch with
`asyncio.create_task` and drain them here after the local DB state has
committed. A failed task therefore must not fail the request — the
authoritative state change already happened — but it must not vanish either:
`asyncio.wait` (the previous idiom) drops task exceptions on the floor, which
made partial Okta failures during membership changes invisible.

Per the concurrency rule in `api/extensions.py`, these tasks only perform
network I/O (Okta calls, notification hooks) — never `db.session` access.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("api")


async def drain_fan_out_tasks(tasks: list[asyncio.Task[Any]], context: str) -> None:
    """Await every fan-out task and log each failure with its context.

    Failures are logged at ERROR (with the traceback) and swallowed: the
    DB commit these tasks trail behind has already happened, so raising
    would turn an Okta/notification straggler into a misleading request
    failure. The syncer reconciles Okta drift on its next run.
    """
    if not tasks:
        return
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            logger.error(
                "Okta/notification task failed during %s; local DB state is already committed",
                context,
                exc_info=result,
            )
