"""Durable delivery of `group_deleted`, the one lifecycle hook nothing else recovers.

`sync_group` sweeps live app groups, so a soft-deleted one is invisible to it: a `group_deleted`
that never reaches the plugin leaves the external group and its members alive while Access shows
the access as revoked. Keeping the hook on the request removes the largest way to lose it but not
every way — it can still raise, and a worker can still die mid-request with the soft delete already
committed.

So `DeleteGroup` records what it owes alongside the soft delete, settles it when the hook succeeds,
and `sync-app-groups` re-delivers whatever is left. These tests cover the record surviving exactly
the failures it exists for, and the retry refusing the cases where delivering would be wrong.
"""

from __future__ import annotations

from typing import Any, Generator

import pytest
from fastapi import FastAPI
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from api.extensions import Db
from api.models import App, AppGroup, OktaGroup, OktaUser, PendingAppGroupDeletion
from api.operations import DeleteGroup, ModifyGroupType, ModifyGroupUsers
from api.operations._lifecycle_outbox import MAX_DELIVERY_ATTEMPTS
from api.plugins.app_group_lifecycle import (
    AppGroupLifecycleContext,
    AppGroupLifecyclePluginMetadata,
    hookimpl,
)
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory, OktaUserFactory


class DeleteRecordingPlugin:
    """Records `group_deleted` calls, and can be told to fail them."""

    ID: str = "delete_outbox_test_plugin"

    def __init__(self) -> None:
        self.deleted: list[tuple[str, tuple[str, ...]]] = []
        self.fail = False

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata:
        return AppGroupLifecyclePluginMetadata(
            id=self.ID, display_name="Delete Outbox Test Plugin", description="Records deletes"
        )

    @hookimpl
    async def group_deleted(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id != self.ID:
            return
        self.deleted.append((group.id, tuple(m.id for m in members)))
        if self.fail:
            raise RuntimeError("external system unavailable")


@pytest.fixture
def delete_plugin(app: FastAPI, mocker: MockerFixture) -> Generator[DeleteRecordingPlugin, None, None]:
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module
    from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginSpec

    instance = DeleteRecordingPlugin()
    pm = pluggy.PluginManager(plugin_module.app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)
    pm.register(instance, name=DeleteRecordingPlugin.ID)

    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    mocker.patch.object(plugin_module, "_cached_plugin_registry", None)

    yield instance

    plugin_module._cached_app_group_lifecycle_hook = None
    plugin_module._cached_plugin_registry = None


async def _app_group(
    db: Db, app_name: str, *, plugin_id: str | None = DeleteRecordingPlugin.ID, name: str | None = None
) -> AppGroup:
    test_app = AppFactory.build(name=app_name, app_group_lifecycle_plugin=plugin_id)
    prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    group = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=name or f"{prefix}Members")
    db.session.add_all([test_app, group])
    await db.session.commit()
    return (
        await db.session.scalars(select(AppGroup).where(AppGroup.id == group.id).options(joinedload(AppGroup.app)))
    ).one()


async def _pending(db: Db) -> list[PendingAppGroupDeletion]:
    # `populate_existing` rather than `expire_all`: an expired attribute read on an AsyncSession
    # needs IO and raises MissingGreenlet, where this refreshes in place from the query result.
    return list(
        (await db.session.scalars(select(PendingAppGroupDeletion).execution_options(populate_existing=True))).all()
    )


# --- recording and settling ----------------------------------------------------


async def test_a_successful_delete_leaves_nothing_owed(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    mocker.patch.object(okta, "delete_group")
    group = await _app_group(db, "SettledApp")

    await DeleteGroup(group=group.id).execute()

    assert [d[0] for d in delete_plugin.deleted] == [group.id]
    assert await _pending(db) == []


async def test_a_failed_hook_leaves_the_delivery_owed(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    """The case the record exists for. `invoke_app_group_lifecycle_hook` never propagates, so the
    delete succeeds and the plugin never hears about it -- and nothing else would ever look."""
    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "FailedHookApp")

    await DeleteGroup(group=group.id).execute()

    rows = await _pending(db)
    assert [(r.group_id, r.plugin_id) for r in rows] == [(group.id, DeleteRecordingPlugin.ID)]


async def test_the_record_carries_the_membership_captured_before_the_delete(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    """By retry time the memberships are ended, so re-deriving them would hand the plugin an empty
    list -- and a plugin that provisions users individually needs to know who to deprovision."""
    mocker.patch.object(okta, "delete_group")
    mocker.patch.object(okta, "remove_user_from_group")
    delete_plugin.fail = True
    group = await _app_group(db, "CapturedMembersApp")
    member = OktaUserFactory.build()
    db.session.add(member)
    await db.session.commit()
    await ModifyGroupUsers(group=group.id, members_to_add=[member.id], sync_to_okta=False).execute()

    await DeleteGroup(group=group.id).execute()

    rows = await _pending(db)
    assert rows[0].member_ids == [member.id]


async def test_a_group_with_no_lifecycle_plugin_owes_nothing(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    mocker.patch.object(okta, "delete_group")
    group = await _app_group(db, "NoPluginApp", plugin_id=None)

    await DeleteGroup(group=group.id).execute()

    assert await _pending(db) == []


# --- re-delivery ---------------------------------------------------------------


async def test_the_sweep_redelivers_and_settles(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    from api.cli import _redeliver_pending_group_deletions

    mocker.patch.object(okta, "delete_group")
    mocker.patch.object(okta, "remove_user_from_group")
    delete_plugin.fail = True
    group = await _app_group(db, "RedeliverApp")
    member = OktaUserFactory.build()
    db.session.add(member)
    await db.session.commit()
    await ModifyGroupUsers(group=group.id, members_to_add=[member.id], sync_to_okta=False).execute()
    await DeleteGroup(group=group.id).execute()
    delete_plugin.deleted.clear()
    delete_plugin.fail = False

    failures = await _redeliver_pending_group_deletions()

    assert failures == 0
    # Re-loaded against the soft-deleted row, with the membership the delete captured.
    assert delete_plugin.deleted == [(group.id, (member.id,))]
    assert await _pending(db) == []


async def test_a_failed_redelivery_counts_against_the_retry_budget(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    from api.cli import _redeliver_pending_group_deletions

    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "RetryBudgetApp")
    await DeleteGroup(group=group.id).execute()

    failures = await _redeliver_pending_group_deletions()

    assert failures == 1
    rows = await _pending(db)
    assert rows[0].attempts == 1
    assert rows[0].last_error is not None and "external system unavailable" in rows[0].last_error
    assert rows[0].last_attempt_at is not None


async def test_the_sweep_gives_up_past_the_attempt_cap(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    """An external system that keeps rejecting the call will not start succeeding by repetition.
    The row stays -- it is the record that an external group may still exist -- but stops being
    retried, so the useful signal is not buried under a failure every run."""
    from api.cli import _redeliver_pending_group_deletions

    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "ExhaustedApp")
    await DeleteGroup(group=group.id).execute()
    rows = await _pending(db)
    rows[0].attempts = MAX_DELIVERY_ATTEMPTS
    await db.session.commit()
    delete_plugin.deleted.clear()

    failures = await _redeliver_pending_group_deletions()

    assert failures == 0
    assert delete_plugin.deleted == []
    assert len(await _pending(db)) == 1


async def test_the_sweep_drops_a_delivery_for_a_group_that_is_no_longer_an_app_group(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    """Converting a group away from an app group hard-deletes the app_group row, so there is
    nothing left to hand the hook and nothing that will change. Stop tracking it."""
    from api.cli import _redeliver_pending_group_deletions

    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "ConvertedAwayApp", name="UnprefixedConvertibleGroup")
    await DeleteGroup(group=group.id).execute()
    # Undo the soft delete so the conversion is permitted, then convert.
    reloaded = (await db.session.scalars(select(OktaGroup).where(OktaGroup.id == group.id))).one()
    reloaded.deleted_at = None
    await db.session.commit()
    await ModifyGroupType(
        group=group.id,
        group_changes=OktaGroup(id=group.id, name="PlainConvertedGroup", description=""),
        current_user_id=None,
    ).execute()
    delete_plugin.deleted.clear()

    failures = await _redeliver_pending_group_deletions()

    assert failures == 0
    assert delete_plugin.deleted == []
    assert await _pending(db) == []


async def test_the_sweep_refuses_to_deliver_to_a_different_plugin(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    """Firing would tell a plugin to delete a group it never created, and the plugin actually owed
    the delivery is no longer reachable through this group. Leave it for an operator."""
    from api.cli import _redeliver_pending_group_deletions

    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "RepointedApp")
    await DeleteGroup(group=group.id).execute()
    app_row = (await db.session.scalars(select(App).where(App.id == group.app_id))).one()
    app_row.app_group_lifecycle_plugin = "some_other_plugin"
    await db.session.commit()
    delete_plugin.deleted.clear()

    failures = await _redeliver_pending_group_deletions()

    assert failures == 1
    assert delete_plugin.deleted == []
    rows = await _pending(db)
    assert len(rows) == 1
    # The reason is durable, so it outlives this run's stderr...
    assert rows[0].last_error is not None and "some_other_plugin" in rows[0].last_error
    # ...but no attempt was made, so the budget is untouched. Counting one would age a live
    # misconfiguration out of the sweep after MAX_DELIVERY_ATTEMPTS runs and stop reporting it.
    assert rows[0].attempts == 0


async def test_re_deleting_a_group_resets_the_retry_budget(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture
) -> None:
    """`DeleteGroup` does not filter `deleted_at`, so an already-deleted group can be deleted again
    and reuses the existing row. That second delete is a fresh obligation: carried-over attempts
    would leave a row that had already exhausted its budget excluded from the sweep forever, so the
    new delete would never be delivered and nothing would say so."""
    from api.cli import _redeliver_pending_group_deletions

    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "ReDeletedApp")
    await DeleteGroup(group=group.id).execute()
    rows = await _pending(db)
    rows[0].attempts = MAX_DELIVERY_ATTEMPTS
    rows[0].last_error = "an older failure"
    await db.session.commit()

    # The same group deleted again.
    await DeleteGroup(group=group.id).execute()

    rows = await _pending(db)
    assert len(rows) == 1
    assert (rows[0].attempts, rows[0].last_error, rows[0].last_attempt_at) == (0, None, None)

    # And so it is eligible for the sweep again, rather than silently past its budget.
    delete_plugin.fail = False
    delete_plugin.deleted.clear()
    assert await _redeliver_pending_group_deletions() == 0
    assert [d[0] for d in delete_plugin.deleted] == [group.id]


async def test_nothing_owed_is_a_no_op(db: Db, delete_plugin: DeleteRecordingPlugin) -> None:
    from api.cli import _redeliver_pending_group_deletions

    assert await _redeliver_pending_group_deletions() == 0


async def test_sync_app_groups_counts_undelivered_deletes_towards_its_exit_status(
    db: Db, delete_plugin: DeleteRecordingPlugin, mocker: MockerFixture, capsys: Any
) -> None:
    """The cronjob has to exit non-zero on an undelivered delete, or a run that left an external
    group orphaned looks like a clean run."""
    from api.cli import _redeliver_pending_group_deletions, _sync_all_app_groups

    mocker.patch.object(okta, "delete_group")
    delete_plugin.fail = True
    group = await _app_group(db, "ExitStatusApp")
    await DeleteGroup(group=group.id).execute()

    failures = await _redeliver_pending_group_deletions()
    failures += await _sync_all_app_groups()

    assert failures > 0
