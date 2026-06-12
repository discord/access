"""Draining helper for the Okta/notification task fan-out in operations.

The fan-out operations (`ModifyGroupUsers`, `ModifyRoleGroups`, `DeleteUser`,
`DeleteGroup`) spawn Okta API calls and notification dispatch with
`asyncio.create_task` and drain them here after the local DB state has
committed. A failed task therefore must not fail the request — the
authoritative state change already happened — but it must not vanish either:
the previous idiom (`asyncio.wait(tasks)` whose result was discarded) dropped
task exceptions on the floor, which made partial Okta failures during
membership changes invisible.

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

    Failures are logged at WARNING (with the traceback) and swallowed: the
    DB commit these tasks trail behind has already happened, so raising
    would turn an Okta/notification straggler into a misleading request
    failure. These are best-effort, on-demand Okta calls and the syncer
    cronjob reconciles any resulting drift on its next run, so they're a
    WARNING rather than an ERROR — an ERROR here is just Sentry noise for a
    condition the system already recovers from eventually.

    Drains with ``asyncio.wait`` (not ``asyncio.gather``) so that a
    cancellation of the awaiting request — client disconnect, request timeout,
    SIGTERM on a rolling deploy — does not tear down these in-flight Okta
    calls: ``gather`` propagates the cancellation to its children, ``wait``
    leaves them running. Letting them finish is the whole point, since the DB
    change they trail is already committed. Exceptions are read off the
    finished tasks rather than dropped.
    """
    if not tasks:
        return
    done, _ = await asyncio.wait(tasks)
    for task in done:
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "Okta/notification task failed during %s; local DB state is already committed, "
                "the syncer will reconcile",
                context,
                exc_info=exc,
            )
