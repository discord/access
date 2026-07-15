"""Draining + deferral helpers for the Okta/notification task fan-out.

The fan-out operations (`ModifyGroupUsers`, `ModifyRoleGroups`, `DeleteUser`,
`DeleteGroup`) commit their local DB state and then spawn Okta API calls and
notification dispatch with `asyncio.create_task`. Those tasks must not fail the
request — the authoritative state change already happened — but they must not
vanish either: the original idiom (`asyncio.wait(tasks)` whose result was
discarded) dropped task exceptions on the floor, which made partial Okta
failures during membership changes invisible.

`drain_fan_out_tasks` awaits a batch and logs each failure. Operations don't call
it directly; they call `defer_or_drain_fan_out`, which either:

- **defers** the batch to a request-scoped collector when a `BackgroundTask`
  drainer is registered for the current request (the HTTP request path, via the
  `defer_fan_out` dependency), so the response returns as soon as the DB commits
  and the Okta/notification tail runs *after* the response is sent; or
- **drains inline** when no collector is set — CLI, syncer, MCP, and tests that
  call `execute()` directly — preserving the await-to-completion behavior there.

Per the concurrency rule in `api/extensions.py`, these tasks only perform
network I/O (Okta calls, async notification hooks), never `db.session` access.
Because the deferred drain runs after the request session is torn down, notify
tasks must carry ORM objects the session has already `expunge`d (detached, with
their attributes loaded), so the hooks read them without touching the session.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from api.plugins import NotificationHook

logger = logging.getLogger("api")

# Batches handed to a request's deferred drain: (tasks, context) tuples, drained
# by `run_deferred_fan_out` from a FastAPI `BackgroundTask` after the response.
_Collected = list[tuple[list["asyncio.Task[Any]"], str]]

# Set per request by the `defer_fan_out` dependency; `None` outside an opted-in
# request (CLI/syncer/MCP/direct-execute), where `defer_or_drain_fan_out` drains
# inline instead.
_deferred_fan_out: contextvars.ContextVar[Optional[_Collected]] = contextvars.ContextVar(
    "access_deferred_fan_out", default=None
)


async def drain_fan_out_tasks(tasks: list["asyncio.Task[Any]"], context: str) -> None:
    """Await every fan-out task and log each failure with its context.

    Failures are logged at ERROR (with the traceback) and swallowed: Access
    surfaces the errors its Okta calls and notification hooks raise rather than
    quietly downgrading them (mirroring the plugin-hook dispatch in
    ``api/plugins/_async_dispatch.py``). The failure never propagates — the DB
    commit these tasks trail behind has already happened, so raising would turn a
    straggler into a misleading request failure, and the syncer cronjob
    reconciles any resulting Okta drift on its next run — but a component that
    expects noisy failures (e.g. Okta connection timeouts) should catch them
    itself rather than rely on the drain downgrading them.

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
            logger.error(
                "Okta/notification task failed during %s; local DB state is already committed, "
                "the syncer will reconcile",
                context,
                exc_info=exc,
            )


def begin_deferred_fan_out() -> tuple[_Collected, "contextvars.Token[Optional[_Collected]]"]:
    """Open a request-scoped fan-out collector and bind it to the ContextVar.

    Returns the (empty) collector list and the reset token. The same list object
    is what operations append to via the ContextVar, so a caller that also holds
    the returned reference (the `BackgroundTask`) sees whatever was appended.
    """
    collected: _Collected = []
    token = _deferred_fan_out.set(collected)
    return collected, token


def end_deferred_fan_out(token: "contextvars.Token[Optional[_Collected]]") -> None:
    """Unbind the collector opened by `begin_deferred_fan_out`."""
    _deferred_fan_out.reset(token)


async def defer_or_drain_fan_out(tasks: list["asyncio.Task[Any]"], context: str) -> None:
    """Hand a fan-out batch to the request's deferred drain, or drain it inline.

    If a collector is bound for the current request, append the batch and return
    immediately — the response is not held on the Okta/notification tail. Outside
    such a request (CLI/syncer/MCP/direct-`execute()`), drain inline so the
    caller still awaits completion.
    """
    if not tasks:
        return
    collected = _deferred_fan_out.get()
    if collected is None:
        await drain_fan_out_tasks(tasks, context)
    else:
        collected.append((tasks, context))


async def run_deferred_fan_out(collected: _Collected) -> None:
    """Drain every batch deferred during a request. Runs from a `BackgroundTask`
    after the response has been sent (and the request session torn down)."""
    for tasks, context in collected:
        await drain_fan_out_tasks(tasks, context)


async def defer_notification(
    session: AsyncSession,
    hook_name: "NotificationHook",
    *,
    detach: Iterable[Any] = (),
    **kwargs: Any,
) -> None:
    """Dispatch a notification hook, deferred to the post-response `BackgroundTask`
    when the request opted into deferral (via the `defer_fan_out` router
    dependency), else drained inline (CLI/syncer/direct-`execute()`).

    All request-path notifications route through here so the HTTP response
    returns as soon as the local DB state commits, rather than blocking on the
    notification's network I/O.

    `detach` names the ORM objects the hook will read; they are expunged (only
    when deferring) so the async hook can read their already-loaded attributes
    after the request session has been expired (the router's `db.expire_all()`)
    and torn down — see `detach_for_deferred_fan_out` and the read-only detached
    contract on `NotificationPluginSpec`. Resolve everything the hook needs
    before calling this (the spawned task must not touch `db.session`).
    """
    # Local import: `send_notification` lives in `api.plugins`, which the
    # operations layer imports — importing it at module scope here would risk a
    # cycle, and it isn't needed until dispatch.
    from api.plugins import send_notification

    detach_for_deferred_fan_out(session, detach)
    task = asyncio.create_task(send_notification(hook_name, **kwargs))
    await defer_or_drain_fan_out([task], f"notification {hook_name}")


def detach_for_deferred_fan_out(session: AsyncSession, objects: Iterable[Any]) -> None:
    """Expunge already-loaded ORM objects so a *deferred* notification task can
    read their attributes after the request session is expired (the router's
    `db.expire_all()`) and torn down (`db.remove()`).

    A deferred notify task holds ORM objects the notification hook reads; the
    hook accesses attributes synchronously, so an expired/detached-then-reloaded
    read would raise `MissingGreenlet`/`DetachedInstanceError` on the dead
    session. Expunging here (before the router expires the session) detaches them
    with their loaded state intact, so the hook reads plain values without any
    session round trip. Only objects still in the session are expunged
    (re-expunging raises); the default relationship cascade is `save-update,
    merge`, which excludes `expunge`, so this detaches exactly the objects passed
    — not their relationship graph.

    No-op when the fan-out is NOT being deferred (CLI/syncer/direct-`execute()`):
    there the drain runs inline against the live session, so detaching would
    needlessly leave the caller holding detached ORM objects it may still mutate.
    """
    if _deferred_fan_out.get() is None:
        return
    for obj in objects:
        if obj is not None and obj in session:
            session.expunge(obj)
