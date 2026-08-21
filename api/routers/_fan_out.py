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
from api.operations._lifecycle_fan_out import (
    begin_deferred_lifecycle,
    end_deferred_lifecycle,
    run_deferred_lifecycle,
)


async def defer_fan_out(background_tasks: BackgroundTasks) -> AsyncGenerator[None, None]:
    """Defer this request's Okta/notification fan-out and app-group-lifecycle hooks to
    post-response tasks.

    `add_task` must run before the endpoint (FastAPI attaches `BackgroundTasks`
    to the response at return time), so the drainers are registered up front over
    the collectors operations fill in. On an endpoint error the drains run inline
    before re-raising: the error response FastAPI builds won't carry our
    background tasks, and the session is still open at that point.

    Lifecycle hooks are registered first. The Okta calls they would otherwise queue behind are
    already in flight -- operations spawn them with `create_task` and only the *await* is
    deferred -- so running the hooks first preserves the ordering they have inline: reconciliation
    races the Okta pushes rather than trailing them.
    """
    lifecycle, lifecycle_token = begin_deferred_lifecycle()
    collected, token = begin_deferred_fan_out()
    background_tasks.add_task(run_deferred_lifecycle, lifecycle)
    background_tasks.add_task(run_deferred_fan_out, collected)
    try:
        yield
    except Exception:
        # Lifecycle fires are dropped rather than replayed. Replay re-loads the *group*, but a
        # membership fire's `members` list is carried verbatim, so a removal the failing request
        # rolled back would still reach the plugin as fact -- and a plugin acting on that
        # deprovisions someone who still has access. Losing the fire only delays reconciliation;
        # reporting a change that never happened does not undo itself.
        lifecycle.clear()
        await run_deferred_fan_out(collected)
        collected.clear()
        raise
    finally:
        end_deferred_fan_out(token)
        end_deferred_lifecycle(lifecycle_token)
