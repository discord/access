"""Request-scoped deferral of the app-group-lifecycle plugin hooks.

A lifecycle hook reconciles an Access app group against an external system, so it does the
slowest I/O on the request path — Okta plus whatever the plugin talks to — and the HTTP response
waits on all of it. This module moves that work to a post-response `BackgroundTask`, the same
place `_fan_out.py` puts Okta calls and notifications.

It deliberately does *not* reuse the machinery in `_fan_out.py`, for three reasons, each
disqualifying on its own:

- `prepare_notification_task` spawns with `asyncio.create_task` at defer time, so the work starts
  while the request session is still live. Lifecycle hooks read and write the DB, and the
  concurrency rule in `api/extensions.py` forbids two coroutines sharing one `AsyncSession`.
  What has to be deferred here is the *work*, not just the await.
- Fires within one request are order-dependent (a single `PUT` can convert a group's type and
  change its membership), and spawned tasks interleave.
- A notification gets a detached, read-only snapshot. These hooks *write*: the whole job of
  `AppGroupLifecycleContext` is persisting `set_status` / `set_config` / `set_group_description`.

So a deferred fire is recorded as plain data — ids and scalars, no ORM references — and replayed
sequentially afterwards against a session this module opens and owns. Replay re-loads the group
by id, which means the hook always observes committed truth rather than a snapshot that may have
moved on, and a group that has since been deleted (or converted out of being an app group) is
skipped instead of reconciled against a row that no longer exists.

Outside an opted-in request — CLI, syncer, MCP, a direct `execute()` in tests —
`defer_or_invoke_lifecycle_hook` invokes inline, exactly as a direct call would.

`group_deleted` is deliberately **not** deferred, at either of its fire sites. Deferral trades the
chance of losing a fire against a faster response, and that trade is only defensible when something
comes back for the work later. Nothing does for a delete: `sync_group` sweeps live app groups, so a
soft-deleted one is invisible to it, and a dropped delete leaves the external group and its members
alive while Access shows the access as revoked. Deleting a group is rare and nobody is waiting on
it, so it stays on the request. `defer_or_invoke_lifecycle_hook` enforces this rather than trusting
call sites to remember.

For every other hook a fire can be lost -- a worker killed between the response and the drain, or a
request that fails after queuing one (see `defer_fan_out`, which drops them rather than report a
change that was rolled back). What that costs depends on the plugin, and the host cannot promise
otherwise:

- A plugin whose `sync_group` is a full, idempotent reconciliation re-converges on the next
  `sync-app-groups` run, so a lost fire costs latency. That is the shape the interface asks for
  and what `app_group_lifecycle_google` does.
- `sync_group` is optional and Access does not police what it does. A plugin that omits it, or
  implements it as something other than a full reconciliation, does not recover.
- One gap no `sync_group` can close even then: a sweep sees only current membership, so a plugin
  that revokes by delta cannot recover a lost `group_members_removed`.

The contract this places on plugin authors lives on `AppGroupLifecyclePluginSpec`.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.extensions import _session_scope, db
from api.models import AppGroup, OktaUser
from api.plugins.app_group_lifecycle import (
    AppGroupLifecycleHook,
    get_app_group_lifecycle_plugin_to_invoke,
    invoke_app_group_lifecycle_hook,
)

logger = logging.getLogger("api")

# Values a deferred fire can carry across the response boundary. Anything else (an ORM instance,
# a collection) would be detached or stale by replay time, so `_snapshot_kwargs` refuses it and
# the fire runs inline instead.
_CARRYABLE = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class _DeferredFire:
    """One lifecycle hook call, recorded in plain-Python form for replay after the response.

    Holds no ORM references, for the same reason `_StatusWrite` in
    `api/plugins/app_group_lifecycle.py` holds none: by replay time the request session that
    loaded them is closed, and every attribute read on a detached, expired instance raises.
    """

    hook_method: AppGroupLifecycleHook
    group_id: str
    # Ids of the users a membership hook was told about, in the order the operation passed them.
    # `None` means the fire carried no `members` kwarg at all, which is distinct from `()`: an
    # empty list is a legitimate payload (deleting a group nobody was in) and the hookspec still
    # requires the argument, so replay has to put it back.
    member_ids: tuple[str, ...] | None
    # The remaining hook kwargs, all scalars — `old_name` / `old_description` today.
    extra: tuple[tuple[str, Any], ...]


_Collected = list[_DeferredFire]

# Set per request by the `defer_fan_out` dependency; `None` outside an opted-in request, where
# `defer_or_invoke_lifecycle_hook` invokes inline instead.
_deferred_lifecycle: contextvars.ContextVar[Optional[_Collected]] = contextvars.ContextVar(
    "access_deferred_lifecycle", default=None
)


def begin_deferred_lifecycle() -> tuple[_Collected, "contextvars.Token[Optional[_Collected]]"]:
    """Open a request-scoped lifecycle collector and bind it to the ContextVar.

    Returns the (empty) collector and the reset token. The same list object is what
    `defer_or_invoke_lifecycle_hook` appends to via the ContextVar, so a caller holding the
    returned reference (the `BackgroundTask`) sees whatever was appended.
    """
    collected: _Collected = []
    token = _deferred_lifecycle.set(collected)
    return collected, token


def end_deferred_lifecycle(token: "contextvars.Token[Optional[_Collected]]") -> None:
    """Unbind the collector opened by `begin_deferred_lifecycle`."""
    _deferred_lifecycle.reset(token)


def _snapshot_kwargs(
    kwargs: dict[str, Any],
) -> Optional[tuple[Optional[tuple[str, ...]], tuple[tuple[str, Any], ...]]]:
    """Split hook kwargs into (member ids, scalar kwargs), or None if something can't be carried.

    `members` is the only ORM-bearing kwarg any lifecycle hookspec takes; it becomes a tuple of
    user ids, or stays `None` when the fire did not carry one at all. Everything else must be a
    scalar. Returning None rather than raising keeps a future kwarg from breaking a request: the
    caller falls back to invoking inline, which is correct — just not deferred.
    """
    member_ids: tuple[str, ...] | None = None
    extra: list[tuple[str, Any]] = []
    for key, value in kwargs.items():
        if key == "members":
            member_ids = tuple(member.id for member in value)
        elif isinstance(value, _CARRYABLE):
            extra.append((key, value))
        else:
            return None
    return member_ids, tuple(extra)


async def defer_or_invoke_lifecycle_hook(
    hook_method: AppGroupLifecycleHook, *, group: Any, **kwargs: Any
) -> list[BaseException]:
    """Record a lifecycle hook fire for the post-response drain, or invoke it inline.

    Inside an opted-in request (the `defer_fan_out` router dependency), the fire is recorded and
    this returns immediately, so the response is not held on the plugin's reconciliation I/O.
    Everywhere else — CLI, syncer, MCP, a direct `execute()` — it invokes inline and behaves
    exactly like calling `invoke_app_group_lifecycle_hook` directly.

    Returns the exceptions the hook raised, so an inline caller can count failures; a deferred
    fire has not run yet and so returns empty. `api/cli.py` is the one caller that acts on the
    result, and it never defers.
    """
    collected = _deferred_lifecycle.get()
    # GROUP_DELETED is never deferred, wherever it is fired from. Replay re-loads the group by id
    # and a deleted group no longer resolves, so a deferred delete would be dropped in silence --
    # and unlike every other hook, nothing would ever revisit it, since `sync_group` sweeps only
    # live groups. Both fire sites call `invoke_app_group_lifecycle_hook` directly; this is here so
    # that a new delete path reaching for the deferring helper gets the safe behaviour rather than
    # a hook that quietly never runs.
    if collected is None or hook_method == AppGroupLifecycleHook.GROUP_DELETED:
        return await invoke_app_group_lifecycle_hook(hook_method, session=db.session, group=group, **kwargs)

    # Resolve the plugin now, on a group that is still live and eager-loaded. Deferring a fire for
    # a group with no lifecycle plugin would cost a re-load only to resolve to the same no-op.
    if get_app_group_lifecycle_plugin_to_invoke(group) is None:
        return []

    snapshot = _snapshot_kwargs(kwargs)
    if snapshot is None:
        logger.error(
            "Cannot defer %s hook for group %s: unsupported kwargs %s; invoking inline",
            hook_method,
            group.id,
            sorted(kwargs),
        )
        return await invoke_app_group_lifecycle_hook(hook_method, session=db.session, group=group, **kwargs)

    member_ids, extra = snapshot
    collected.append(
        _DeferredFire(
            hook_method=hook_method,
            group_id=group.id,
            member_ids=member_ids,
            extra=extra,
        )
    )
    return []


async def _reload_group(session: AsyncSession, fire: _DeferredFire) -> Optional[AppGroup]:
    """Re-load the fire's group, or None if there is no longer a live app group to reconcile.

    Selecting `AppGroup` rather than polymorphic `OktaGroup` makes the "still an app group" check
    fall out of the query: a group converted away from an app group has had its `app_group` row
    deleted and simply does not match. `joinedload(AppGroup.app)` supplies the relationship
    `get_app_group_lifecycle_plugin_to_invoke` reads, which is `lazy="raise_on_sql"`.
    `populate_existing` because this session outlives individual hook transactions and
    `expire_on_commit=False` leaves instances loaded before a commit unexpired.

    Filtering `deleted_at` is safe precisely because `group_deleted` never gets here -- see the
    guard in `defer_or_invoke_lifecycle_hook`. Every hook that does is about a group that should
    still exist, so a soft-deleted row means the work is moot.
    """
    return (
        await session.scalars(
            select(AppGroup)
            .where(AppGroup.id == fire.group_id)
            .where(AppGroup.deleted_at.is_(None))
            .options(joinedload(AppGroup.app))
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def _reload_members(session: AsyncSession, member_ids: tuple[str, ...]) -> list[OktaUser]:
    """Re-load the users a membership fire named, preserving the operation's order.

    Deliberately unfiltered by `deleted_at`: the hook is being told about a membership change that
    happened, and a user soft-deleted in the meantime does not un-happen it.
    """
    users = (await session.scalars(select(OktaUser).where(OktaUser.id.in_(member_ids)))).all()
    by_id = {user.id: user for user in users}
    return [by_id[member_id] for member_id in member_ids if member_id in by_id]


async def run_deferred_lifecycle(collected: _Collected) -> None:
    """Replay every lifecycle fire deferred during a request, in order.

    Runs from a `BackgroundTask` after the response has been sent, by which point
    `RequestIdMiddleware` has already torn the request session down — so this opens its own
    session under its own scope.

    Binding `_session_scope` (rather than just building a session) is load-bearing.
    `AppGroupLifecycleContext.set_group_description` delegates to `ModifyGroupDetails`, which
    reaches for the ambient `db.session`; if that resolved to a different session than the one the
    context holds, the operation's writes would land in a transaction nobody commits. Binding the
    scope first makes `db.session` *be* this session.

    Fires run one at a time: they share the session, which must not be used concurrently, and a
    single request's fires are order-dependent. Nothing here propagates — the operations that
    queued these fires committed long ago, and the `sync-app-groups` cronjob re-converges anything
    that fails.
    """
    if not collected:
        return

    token = _session_scope.set(f"deferred-lifecycle-{uuid.uuid4().hex}")
    try:
        session = db.session
        for fire in collected:
            context = f"deferred {fire.hook_method} hook for group {fire.group_id}"
            try:
                group = await _reload_group(session, fire)
                if group is None:
                    logger.info("Skipping %s: the group is gone or is no longer an app group", context)
                    continue
                kwargs: dict[str, Any] = dict(fire.extra)
                if fire.member_ids is not None:
                    kwargs["members"] = await _reload_members(session, fire.member_ids)
                # Never raises on a plugin's behalf; it logs failures and re-applies the plugin's
                # durable status itself, so its return value is nothing this drain can act on.
                await invoke_app_group_lifecycle_hook(fire.hook_method, session=session, group=group, **kwargs)
            except Exception:
                logger.exception("Failed to run %s; the sync-app-groups cronjob will reconcile", context)
    finally:
        try:
            await db.remove()
        finally:
            _session_scope.reset(token)
