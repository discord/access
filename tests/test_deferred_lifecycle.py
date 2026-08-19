"""Deferring app-group-lifecycle plugin hooks to a post-response BackgroundTask.

A lifecycle hook reconciles a group against an external system, so leaving it on the request path
made every group create/update/membership change wait on that system's I/O. `defer_fan_out` now
collects the fires and `run_deferred_lifecycle` replays them after the response.

What makes this different from the notification deferral in `test_deferred_fan_out.py` — and what
these tests pin — is that the hooks *write*, so a fire cannot carry a detached snapshot. It carries
ids, and replay re-loads against a session the drain opens and owns. That buys a property worth
protecting: a fire always sees committed truth, and a group that has since been deleted, or
converted out of being an app group, is skipped rather than reconciled against a row that is no
longer there.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Generator

import pytest
from fastapi import FastAPI
from okta.models.group import Group
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from api.extensions import Db, _session_scope
from api.models import AppGroup, OktaGroup, OktaUser
from api.operations import DeleteGroup, ModifyGroupType, ModifyGroupUsers
from api.operations._lifecycle_fan_out import (
    _DeferredFire,
    _snapshot_kwargs,
    begin_deferred_lifecycle,
    defer_or_invoke_lifecycle_hook,
    end_deferred_lifecycle,
    run_deferred_lifecycle,
)
from api.plugins.app_group_lifecycle import (
    AppGroupLifecycleContext,
    AppGroupLifecycleHook,
    AppGroupLifecyclePluginMetadata,
    hookimpl,
)
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory, OktaUserFactory


class RecordingPlugin:
    """Records every lifecycle hook it receives, and can be told to fail on specific groups."""

    ID: str = "deferred_test_plugin"

    def __init__(self) -> None:
        # (hook name, group id, group name, member ids) per call, in call order.
        self.calls: list[tuple[str, str, str, tuple[str, ...]]] = []
        # Group names whose hooks raise, so a failing fire's effect on its siblings is observable.
        self.fail_group_names: set[str] = set()
        # Set to a description to have group_updated push one through `ctx.set_group_description`.
        self.description_to_set: str | None = None
        # Set to a status property name to have group_created write it through the context.
        self.status_to_set: str | None = None

    def _record(self, hook: str, group: AppGroup, members: list[OktaUser] | None = None) -> None:
        self.calls.append((hook, group.id, group.name, tuple(m.id for m in members or [])))
        if group.name in self.fail_group_names:
            raise RuntimeError(f"{hook} failed for {group.name}")

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata:
        return AppGroupLifecyclePluginMetadata(
            id=self.ID, display_name="Deferred Test Plugin", description="Records lifecycle calls"
        )

    @hookimpl
    async def group_created(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id != self.ID:
            return
        if self.status_to_set is not None:
            ctx.set_status(group, self.status_to_set, True)
        self._record("group_created", group)

    @hookimpl
    async def group_updated(
        self,
        ctx: AppGroupLifecycleContext,
        group: AppGroup,
        old_name: str,
        old_description: str,
        plugin_id: str | None,
    ) -> None:
        if plugin_id != self.ID:
            return
        if self.description_to_set is not None:
            await ctx.set_group_description(group, self.description_to_set)
        self._record("group_updated", group)

    @hookimpl
    async def group_deleted(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id != self.ID:
            return
        self._record("group_deleted", group, members)

    @hookimpl
    async def group_members_added(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id != self.ID:
            return
        self._record("group_members_added", group, members)

    @hookimpl
    async def group_members_removed(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id != self.ID:
            return
        self._record("group_members_removed", group, members)


@pytest.fixture
def recording_plugin(app: FastAPI, mocker: MockerFixture) -> Generator[RecordingPlugin, None, None]:
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module
    from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginSpec

    instance = RecordingPlugin()
    pm = pluggy.PluginManager(plugin_module.app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)
    pm.register(instance, name=RecordingPlugin.ID)

    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    mocker.patch.object(plugin_module, "_cached_plugin_registry", None)

    yield instance

    plugin_module._cached_app_group_lifecycle_hook = None
    plugin_module._cached_plugin_registry = None


@pytest.fixture
def collector() -> Generator[list[_DeferredFire], None, None]:
    """Bind a lifecycle collector, as the `defer_fan_out` router dependency does per request."""
    collected, token = begin_deferred_lifecycle()
    try:
        yield collected
    finally:
        end_deferred_lifecycle(token)


async def _app_group(
    db: Db,
    app_name: str,
    suffix: str = "Group",
    *,
    plugin_id: str | None = RecordingPlugin.ID,
    name: str | None = None,
) -> AppGroup:
    test_app = AppFactory.build(name=app_name, app_group_lifecycle_plugin=plugin_id)
    prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    group = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=name or f"{prefix}{suffix}")
    db.session.add_all([test_app, group])
    await db.session.commit()
    # The hook path reads `group.app`, which is lazy="raise_on_sql".
    return (
        await db.session.scalars(select(AppGroup).where(AppGroup.id == group.id).options(joinedload(AppGroup.app)))
    ).one()


# --- defer vs. invoke inline ---------------------------------------------------


async def test_invokes_inline_when_no_collector_is_bound(db: Db, recording_plugin: RecordingPlugin) -> None:
    """CLI, syncer, MCP and direct `execute()` have no collector, and must behave as before."""
    group = await _app_group(db, "InlineApp")

    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)

    assert [c[0] for c in recording_plugin.calls] == ["group_created"]


async def test_defers_until_the_drain_runs(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    group = await _app_group(db, "DeferApp")

    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)

    # Nothing has run yet — this is the point of the change.
    assert recording_plugin.calls == []
    assert [(f.hook_method, f.group_id) for f in collector] == [(AppGroupLifecycleHook.GROUP_CREATED, group.id)]

    await run_deferred_lifecycle(collector)

    assert [(c[0], c[1]) for c in recording_plugin.calls] == [("group_created", group.id)]


async def test_does_not_defer_a_group_with_no_lifecycle_plugin(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    """Queuing a fire for an unconfigured group would buy a re-load only to resolve to a no-op."""
    group = await _app_group(db, "NoPluginApp", plugin_id=None)

    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)

    assert collector == []
    assert recording_plugin.calls == []


def test_snapshot_kwargs_refuses_what_it_cannot_carry() -> None:
    """Scalars and `members` are carryable; anything else would be stale or detached by replay."""
    assert _snapshot_kwargs({"old_name": "a", "old_description": "", "unset": None}) == (
        None,
        (("old_name", "a"), ("old_description", ""), ("unset", None)),
    )
    # An empty members list is not the same as no members kwarg: the hookspec still requires the
    # argument, so it has to survive the round trip as `()` rather than collapsing to absent.
    assert _snapshot_kwargs({"members": []}) == ((), ())
    assert _snapshot_kwargs({"some_orm_object": object()}) is None


async def test_falls_back_to_inline_when_a_kwarg_cannot_be_carried(
    db: Db,
    recording_plugin: RecordingPlugin,
    collector: list[_DeferredFire],
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A kwarg that can't survive the boundary costs deferral, never correctness."""
    group = await _app_group(db, "UncarryableApp")
    invoke = mocker.patch("api.operations._lifecycle_fan_out.invoke_app_group_lifecycle_hook", return_value=[])

    with caplog.at_level(logging.ERROR, logger="api"):
        await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_UPDATED, group=group, future_kwarg=object())

    assert collector == []
    invoke.assert_awaited_once()
    assert "Cannot defer" in caplog.text


# --- replay ---------------------------------------------------------------------


async def test_replay_skips_a_group_deleted_after_the_response(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    group = await _app_group(db, "VanishedApp")
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)

    group.deleted_at = datetime.now(UTC)
    await db.session.commit()

    await run_deferred_lifecycle(collector)

    assert recording_plugin.calls == []


async def test_replay_skips_a_group_converted_out_of_being_an_app_group(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    """Selecting AppGroup (not polymorphic OktaGroup) is what makes this fall out of the query."""
    group = await _app_group(db, "ConvertedApp", name="ConvertedAwayGroup")
    await defer_or_invoke_lifecycle_hook(
        AppGroupLifecycleHook.GROUP_UPDATED, group=group, old_name="x", old_description=""
    )

    await ModifyGroupType(
        group=group.id,
        group_changes=OktaGroup(id=group.id, name="PlainGroup", description=""),
        current_user_id=None,
    ).execute()

    recording_plugin.calls.clear()  # the conversion fires group_deleted inline; not what's under test
    await run_deferred_lifecycle(collector)

    assert recording_plugin.calls == []


async def test_group_deleted_replays_against_the_soft_deleted_row(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    """DeleteGroup soft-deletes before it fires, so this replay must not filter `deleted_at`."""
    group = await _app_group(db, "SoftDeletedApp")
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_DELETED, group=group, members=[])
    assert collector[0].allow_deleted is True

    group.deleted_at = datetime.now(UTC)
    await db.session.commit()

    await run_deferred_lifecycle(collector)

    # An empty membership survives as an empty tuple, not as an absent kwarg — the hookspec
    # requires `members`, so dropping it would fail the call for every empty group.
    assert [(c[0], c[1], c[3]) for c in recording_plugin.calls] == [("group_deleted", group.id, ())]


async def test_deleting_a_group_defers_the_membership_it_captured(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire], mocker: MockerFixture
) -> None:
    """DeleteGroup captures the membership *before* ending it, because a plugin that provisioned
    users individually cannot recover the list afterwards. Deferral must not cost that: the fire
    carries the ids it was handed, so re-deriving members from the group at replay time — when the
    memberships are already ended — would quietly hand the plugin an empty list."""
    mocker.patch.object(okta, "delete_group")
    group = await _app_group(db, "DeleteMembersApp")
    member = OktaUserFactory.build()
    db.session.add(member)
    await db.session.commit()
    await ModifyGroupUsers(group=group.id, members_to_add=[member.id], sync_to_okta=False).execute()

    await DeleteGroup(group=group.id).execute()

    # Queued, not fired, and the membership is already gone by now.
    assert recording_plugin.calls == []
    assert collector[0].member_ids == (member.id,)

    await run_deferred_lifecycle(collector)

    assert [(c[0], c[3]) for c in recording_plugin.calls] == [("group_deleted", (member.id,))]


async def test_replays_in_the_order_they_were_fired(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    """One request's fires are order-dependent; the drain is sequential, not a fan-out."""
    first = await _app_group(db, "OrderAppOne")
    second = await _app_group(db, "OrderAppTwo")

    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=first)
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=second)
    await defer_or_invoke_lifecycle_hook(
        AppGroupLifecycleHook.GROUP_UPDATED, group=first, old_name="old", old_description=""
    )

    await run_deferred_lifecycle(collector)

    assert [(c[0], c[2]) for c in recording_plugin.calls] == [
        ("group_created", first.name),
        ("group_created", second.name),
        ("group_updated", first.name),
    ]


async def test_a_failing_fire_does_not_strand_the_rest(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    failing = await _app_group(db, "FailingApp")
    healthy = await _app_group(db, "HealthyApp")
    recording_plugin.fail_group_names.add(failing.name)

    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=failing)
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=healthy)

    await run_deferred_lifecycle(collector)

    assert [c[2] for c in recording_plugin.calls] == [failing.name, healthy.name]


async def test_members_are_reloaded_in_the_order_the_operation_passed_them(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    group = await _app_group(db, "MembersApp")
    members = [OktaUserFactory.build(), OktaUserFactory.build(), OktaUserFactory.build()]
    db.session.add_all(members)
    await db.session.commit()

    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_MEMBERS_ADDED, group=group, members=members)
    assert collector[0].member_ids == tuple(m.id for m in members)

    await run_deferred_lifecycle(collector)

    assert recording_plugin.calls[0][3] == tuple(m.id for m in members)


async def test_context_writes_land_through_the_drain_session(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire], mocker: MockerFixture
) -> None:
    """`ctx.set_group_description` delegates to ModifyGroupDetails, which reaches for the ambient
    `db.session`. If the drain did not bind `_session_scope`, that would be a *different* session
    from the one the context holds and the write would land in a transaction nobody commits —
    silently persisting nothing, which no other assertion here would catch."""
    mocker.patch.object(okta, "update_group")
    group = await _app_group(db, "ScopeApp")
    recording_plugin.description_to_set = "adopted from the external system"

    await defer_or_invoke_lifecycle_hook(
        AppGroupLifecycleHook.GROUP_UPDATED, group=group, old_name=group.name, old_description=""
    )
    await run_deferred_lifecycle(collector)

    # Re-read through a session that never saw the write, so this can only pass if it committed.
    reloaded = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.id == group.id).execution_options(populate_existing=True)
        )
    ).one()
    assert reloaded.description == "adopted from the external system"


async def test_drain_leaves_no_session_bound_to_its_scope(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    """The drain owns the session it opens; leaking one per request would be a slow pool leak."""
    group = await _app_group(db, "ScopeCleanupApp")
    await defer_or_invoke_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)
    scope_before = _session_scope.get()

    await run_deferred_lifecycle(collector)

    assert _session_scope.get() == scope_before


# --- through the router ---------------------------------------------------------


async def test_group_create_responds_before_the_hook_runs(
    client: AsyncClient, db: Db, recording_plugin: RecordingPlugin, mocker: MockerFixture, url_for: Any
) -> None:
    """The whole point, end to end: the response is built before the plugin is asked to reconcile.

    Also pins the user-visible consequence — status the hook writes is absent from the create
    response and shows up on the next read, which is what the group page polls for.
    """
    mocker.patch.object(okta, "create_group", return_value=Group.from_dict({"id": "okta-created-group"}))
    recording_plugin.status_to_set = "reconciled"
    test_app = AppFactory.build(name="RouterApp", app_group_lifecycle_plugin=RecordingPlugin.ID)
    db.session.add(test_app)
    await db.session.commit()

    # Observe how many hooks had run at the moment the drain started. ASGITransport runs background
    # tasks before returning, so without this the assertions below would also pass inline.
    calls_when_drain_started: list[int] = []
    real_drain = run_deferred_lifecycle

    async def spy(collected: list[_DeferredFire]) -> None:
        calls_when_drain_started.append(len(recording_plugin.calls))
        await real_drain(collected)

    mocker.patch("api.routers._fan_out.run_deferred_lifecycle", spy)

    response = await client.post(
        url_for("api-groups.groups"),
        json={
            "type": "app_group",
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}RouterApp{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Members",
            "description": "",
            "app_id": test_app.id,
        },
    )

    assert response.status_code == 201
    group_id = response.json()["id"]
    # The hook had not run when the post-response drain began, and the drain is what ran it.
    assert calls_when_drain_started == [0]
    assert [(c[0], c[1]) for c in recording_plugin.calls] == [("group_created", group_id)]

    # So the create response cannot carry the hook's status write, but the next read does.
    assert response.json().get("plugin_data") in (None, {})
    follow_up = await client.get(url_for("api-groups.group_by_id", group_id=group_id))
    assert follow_up.json()["plugin_data"][RecordingPlugin.ID]["status"] == {"reconciled": True}


async def test_group_type_conversion_fires_group_deleted_inline(
    db: Db, recording_plugin: RecordingPlugin, collector: list[_DeferredFire]
) -> None:
    """The one carve-out: after conversion there is no app_group row left to re-load, so deferring
    this fire would drop it silently."""
    group = await _app_group(db, "CarveOutApp", name="CarveOutGroup")

    await ModifyGroupType(
        group=group.id,
        group_changes=OktaGroup(id=group.id, name="ConvertedPlainGroup", description=""),
        current_user_id=None,
    ).execute()

    # Fired during the operation, not queued for the drain.
    assert [c[0] for c in recording_plugin.calls] == ["group_deleted"]
    assert collector == []
