"""Durable delivery for the one lifecycle hook nothing else can recover.

`sync_group` is the recovery path for a lost lifecycle fire, and it sweeps *live* app groups — so a
soft-deleted one is invisible to it. If `group_deleted` never reaches the plugin, the external group
and its members stay alive with nothing scanning for them, while Access believes the access was
revoked. That is the one gap no plugin can close on its own, however well it implements
`sync_group`.

Keeping the hook on the request (rather than deferring it) removes the largest way to lose it, but
not every way. `invoke_app_group_lifecycle_hook` never propagates, so a hook that raises is logged,
rolled back, and gone; and a worker that dies mid-request loses the call while the soft delete it
trails is already committed. Both leave the same orphan.

So this hook's delivery is *recorded* rather than inferred. `DeleteGroup` writes a
`PendingAppGroupDeletion` row in the same transaction as the soft delete — which is what makes the
record survive the failure it exists for — and the row is dropped once the hook succeeds.
`sync-app-groups` retries what is left.

Scoped to `group_deleted` on purpose. Every other hook is recoverable by a plugin whose `sync_group`
is a full reconciliation, which is what `AppGroupLifecyclePluginSpec` asks for; making the host
guarantee delivery of all of them would mean a DB write per fire on the request path, for a
guarantee the interface already asks plugins to provide. The one fire this still cannot cover is
`ModifyGroupType`'s: it hard-deletes the `app_group` row, so no retry has an `AppGroup` to hand the
hook.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.models import AppGroup, OktaGroup, OktaUser, PendingAppGroupDeletion
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_plugin_to_invoke

logger = logging.getLogger("api")

# How many times the sweep will re-attempt one delivery before leaving it alone. Past this the row
# stays, carrying `last_error`, as the record of a delete that needs a human rather than another
# retry -- an external group whose API keeps rejecting the call will never succeed by repetition,
# and retrying it every run buries the useful signal.
MAX_DELIVERY_ATTEMPTS = 10


async def record_pending_deletion(session: AsyncSession, group: OktaGroup, members: Sequence[OktaUser]) -> None:
    """Note that `group_deleted` is owed to `group`'s plugin, if one applies.

    Takes any group: `get_app_group_lifecycle_plugin_to_invoke` is what narrows it, returning None
    for anything that is not an app group with a plugin configured.

    Call this in the same transaction as the soft delete, *before* the commit that persists it: a
    row committed alongside the deletion is exactly what survives a worker dying before the hook
    runs. No-op when no lifecycle plugin applies.

    `members` is stored as ids rather than re-derived at retry time. By then the memberships are
    ended, and a plugin that provisions users individually needs to know who to deprovision.
    """
    plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
    if plugin_id is None:
        return

    # `DeleteGroup` does not filter `deleted_at` when it loads the group, so deleting an
    # already-soft-deleted one runs the whole operation again and lands here. Reuse the row rather
    # than tripping the (group_id, plugin_id) uniqueness.
    existing = (
        await session.scalars(
            select(PendingAppGroupDeletion)
            .where(PendingAppGroupDeletion.group_id == group.id)
            .where(PendingAppGroupDeletion.plugin_id == plugin_id)
        )
    ).one_or_none()
    if existing is not None:
        # A fresh delete is a fresh obligation, so the retry budget resets with it. Carrying the old
        # count over would let a row that had already exhausted it stay excluded from the sweep
        # forever -- the new delete would never be delivered, and nothing would say so.
        existing.member_ids = [member.id for member in members]
        existing.attempts = 0
        existing.last_attempt_at = None
        existing.last_error = None
        return

    session.add(
        PendingAppGroupDeletion(
            group_id=group.id,
            plugin_id=plugin_id,
            member_ids=[member.id for member in members],
        )
    )


async def settle_pending_deletion(session: AsyncSession, group_id: str, plugin_id: str) -> None:
    """Drop the delivery record for a `group_deleted` that has now succeeded.

    Commits on its own: the hook's own transaction was already committed by
    `invoke_app_group_lifecycle_hook`, and leaving the row behind would have the sweep redeliver a
    hook that worked. A no-op when there is no row -- `ModifyGroupType`'s inline fire never records
    one, since nothing could retry it.
    """
    await session.execute(
        delete(PendingAppGroupDeletion)
        .where(PendingAppGroupDeletion.group_id == group_id)
        .where(PendingAppGroupDeletion.plugin_id == plugin_id)
    )
    await session.commit()


async def outstanding_deletions(session: AsyncSession) -> list[PendingAppGroupDeletion]:
    """Deliveries still owed and still worth attempting, oldest first.

    Rows past `MAX_DELIVERY_ATTEMPTS` are excluded rather than deleted: they are the record that a
    group's external counterpart may still exist, which is precisely the thing an operator needs to
    see.
    """
    return list(
        (
            await session.scalars(
                select(PendingAppGroupDeletion)
                .where(PendingAppGroupDeletion.attempts < MAX_DELIVERY_ATTEMPTS)
                .order_by(PendingAppGroupDeletion.created_at, PendingAppGroupDeletion.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def load_deletion_target(
    session: AsyncSession, pending: PendingAppGroupDeletion
) -> tuple[Optional[AppGroup], list[OktaUser]]:
    """Re-load what a retry needs: the soft-deleted app group, and the users it recorded.

    Deliberately unfiltered by `deleted_at` on both. The group is soft-deleted by definition here --
    that is the whole point -- and a member soft-deleted since does not un-happen their membership.
    Returns `(None, [])` when the group is no longer an app group at all, which the caller treats as
    nothing left to deliver.
    """
    group = (
        await session.scalars(
            select(AppGroup)
            .where(AppGroup.id == pending.group_id)
            .options(joinedload(AppGroup.app))
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if group is None:
        return None, []

    member_ids: list[str] = list(pending.member_ids or [])
    if not member_ids:
        return group, []
    users = (await session.scalars(select(OktaUser).where(OktaUser.id.in_(member_ids)))).all()
    by_id = {user.id: user for user in users}
    return group, [by_id[member_id] for member_id in member_ids if member_id in by_id]


async def record_delivery_failure(
    session: AsyncSession, pending: PendingAppGroupDeletion, error: str, *, count_attempt: bool = True
) -> None:
    """Record why a delivery did not happen, and by default count it against the retry budget.

    Pass ``count_attempt=False`` when the hook was never actually called -- the sweep declined to
    fire it -- so the reason is durable and visible on the row without ageing it out of
    ``outstanding_deletions``. That distinction matters for a blocked delivery: it needs a human
    rather than another attempt, and a misconfiguration that quietly stopped being reported after
    ten runs would be worse than one reported every run.

    ``last_attempt_at`` is set either way; for a blocked row it reads as when the sweep last looked.
    """
    if count_attempt:
        pending.attempts += 1
    pending.last_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
    pending.last_error = error[:1024]
    session.add(pending)
    await session.commit()
