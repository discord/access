"""Request-scoped deferral of the Okta/notification fan-out (TODO 10).

`defer_fan_out` is a router-level dependency for the mutating routers whose
operations fan out Okta calls and notification dispatch (`ModifyGroupUsers`,
`ModifyRoleGroups`, `DeleteGroup`, and their composers). It runs that tail in a
FastAPI `BackgroundTask` after the response, so the HTTP response returns as soon
as the local DB state commits instead of blocking on the Okta round trips.

Operations append their fan-out batches to the request-scoped collector via
`defer_or_drain_fan_out`; a route without this dependency simply drains inline
(correct, just not deferred).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import BackgroundTasks

from api.operations._fan_out import (
    begin_deferred_fan_out,
    end_deferred_fan_out,
    run_deferred_fan_out,
)


async def defer_fan_out(background_tasks: BackgroundTasks) -> AsyncGenerator[None, None]:
    """Defer this request's Okta/notification fan-out to a post-response task.

    `add_task` must run before the endpoint (FastAPI attaches `BackgroundTasks`
    to the response at return time), so the drainer is registered up front over
    the collector operations fill in. On an endpoint error the drain runs inline
    before re-raising: the error response FastAPI builds won't carry our
    background task, and the session is still open at that point.
    """
    collected, token = begin_deferred_fan_out()
    background_tasks.add_task(run_deferred_fan_out, collected)
    try:
        yield
    except Exception:
        await run_deferred_fan_out(collected)
        collected.clear()
        raise
    finally:
        end_deferred_fan_out(token)
