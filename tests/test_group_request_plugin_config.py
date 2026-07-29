import logging
from typing import Any, Callable, Generator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from okta.models import Group as OktaSdkGroup
from pydantic import TypeAdapter
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, AppGroup, GroupRequest, OktaUser, OktaUserGroupMember
from api.operations import ApproveGroupRequest, CreateGroupRequest
from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginFilteringError, hookimpl
from api.schemas import EventType
from api.schemas.requests_schemas import CreateGroupRequestBody, ResolveGroupRequestBody
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory, OktaUserFactory
from tests.test_app_group_lifecycle_plugin import DummyPlugin

UNREGISTERED_PLUGIN_ID = "unregistered_plugin"


@pytest.fixture
def test_plugin(app: FastAPI, mocker: MockerFixture) -> Generator[DummyPlugin, None, None]:
    """Register DummyPlugin (id 'test_plugin') as the app group lifecycle plugin."""
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module
    from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginSpec

    instance = DummyPlugin()
    pm = pluggy.PluginManager(plugin_module.app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)
    pm.register(instance, name=DummyPlugin.ID)
    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    mocker.patch.object(plugin_module, "_cached_plugin_registry", None)
    yield instance
    plugin_module._cached_app_group_lifecycle_hook = None
    plugin_module._cached_plugin_registry = None


async def _make_app_with_plugin(db: Db) -> Any:
    app_obj = await AppFactory.create_async()
    app_obj.app_group_lifecycle_plugin = DummyPlugin.ID
    db.session.add(app_obj)
    await db.session.commit()
    return app_obj


async def _make_app_with_unregistered_plugin(db: Db) -> Any:
    """An app whose configured lifecycle plugin id isn't registered in this
    deployment — e.g. an operator dropped the plugin while apps still referenced
    it. Hook calls filtered to that id get no responses, so the plugin helpers
    raise AppGroupLifecyclePluginFilteringError, which is not a ValueError."""
    app_obj = await AppFactory.create_async()
    app_obj.app_group_lifecycle_plugin = UNREGISTERED_PLUGIN_ID
    db.session.add(app_obj)
    await db.session.commit()
    return app_obj


async def _make_app_owner(db: Db, app_obj: Any) -> OktaUser:
    """Create a user and make them an owner of ``app_obj`` via its owner group,
    so a group request they file auto-approves."""
    owner = await OktaUserFactory.create_async()
    db.session.add(owner)
    owner_group = await AppGroupFactory.create_async(
        name=(
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}"
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ),
        app_id=app_obj.id,
        is_owner=True,
    )
    db.session.add(owner_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=owner.id, group_id=owner_group.id, is_owner=True))
    await db.session.commit()
    return owner


class _StatusWritingPlugin(DummyPlugin):
    """A DummyPlugin whose ``group_created`` mutates the group's config dict in
    place. Used to prove ApproveGroupRequest deep-copies the request's
    plugin_data onto the new group: without that copy, this mutation would leak
    back into the persisted request record."""

    @hookimpl
    async def group_created(self, session: AsyncSession, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        group.plugin_data[self.ID]["configuration"]["group_id"] = "mutated-by-hook"
        self.group_created_calls.append(group.id)


@pytest.fixture
def mutating_plugin(app: FastAPI, mocker: MockerFixture) -> Generator[_StatusWritingPlugin, None, None]:
    """Register _StatusWritingPlugin under the test plugin id."""
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module
    from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginSpec

    instance = _StatusWritingPlugin()
    pm = pluggy.PluginManager(plugin_module.app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)
    pm.register(instance, name=DummyPlugin.ID)
    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    mocker.patch.object(plugin_module, "_cached_plugin_registry", None)
    yield instance
    plugin_module._cached_app_group_lifecycle_hook = None
    plugin_module._cached_plugin_registry = None


# --- Model -----------------------------------------------------------------


async def test_group_request_plugin_data_defaults_to_empty_dict(app: FastAPI, db: Db) -> None:
    user: OktaUser = await OktaUserFactory.create_async()
    db.session.add(user)
    await db.session.commit()

    gr = GroupRequest(
        id="reqplugindata0000001",
        status=AccessRequestStatus.PENDING,
        requester_user_id=user.id,
        requested_group_name="Test Group",
        requested_group_type="okta_group",
    )
    db.session.add(gr)
    await db.session.commit()
    await db.session.refresh(gr)

    assert gr.requested_plugin_data == {}
    assert gr.resolved_plugin_data == {}


async def test_group_request_plugin_data_round_trips(app: FastAPI, db: Db) -> None:
    user: OktaUser = await OktaUserFactory.create_async()
    db.session.add(user)
    await db.session.commit()

    payload = {"test_plugin": {"configuration": {"group_id": "g-123", "region": "us"}}}
    gr = GroupRequest(
        id="reqplugindata0000002",
        status=AccessRequestStatus.PENDING,
        requester_user_id=user.id,
        requested_group_name="Test Group 2",
        requested_group_type="app_group",
        requested_plugin_data=payload,
    )
    db.session.add(gr)
    await db.session.commit()
    await db.session.refresh(gr)

    assert gr.requested_plugin_data == payload
    assert gr.resolved_plugin_data == {}


# --- Schemas ---------------------------------------------------------------


def test_app_group_body_accepts_requested_plugin_data() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(CreateGroupRequestBody)
    body = adapter.validate_python(
        {
            "requested_group_type": "app_group",
            "requested_group_name": "App-Foo-Admins",
            "requested_group_description": "desc",
            "requested_app_id": "app00000000000000001",
            "requested_plugin_data": {"test_plugin": {"configuration": {"group_id": "g-1"}}},
        }
    )
    assert body.requested_plugin_data == {"test_plugin": {"configuration": {"group_id": "g-1"}}}


def test_app_group_body_defaults_plugin_data_to_empty() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(CreateGroupRequestBody)
    body = adapter.validate_python(
        {
            "requested_group_type": "app_group",
            "requested_group_name": "App-Foo-Admins",
            "requested_group_description": "desc",
            "requested_app_id": "app00000000000000001",
        }
    )
    assert body.requested_plugin_data == {}


def test_resolve_body_accepts_resolved_plugin_data() -> None:
    body = ResolveGroupRequestBody.model_validate(
        {"approved": True, "resolved_plugin_data": {"test_plugin": {"configuration": {"group_id": "g-2"}}}}
    )
    assert body.resolved_plugin_data == {"test_plugin": {"configuration": {"group_id": "g-2"}}}


# --- CreateGroupRequest operation ------------------------------------------


async def test_create_group_request_persists_requested_plugin_data(app: FastAPI, db: Db) -> None:
    user: OktaUser = await OktaUserFactory.create_async()
    db.session.add(user)
    await db.session.commit()
    app_obj = await _make_app_with_plugin(db)

    payload = {"test_plugin": {"configuration": {"group_id": "g-9"}}}
    gr = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data=payload,
    ).execute()

    assert gr is not None
    assert gr.requested_plugin_data == payload


# --- POST validation (HTTP) ------------------------------------------------


async def test_post_app_group_request_rejects_missing_required_plugin_config(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
) -> None:
    user: OktaUser = await OktaUserFactory.create_async()
    db.session.add(user)
    await db.session.commit()
    mock_user(user)
    app_obj = await _make_app_with_plugin(db)

    resp = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_type": "app_group",
            "requested_group_name": f"App-{app_obj.name}-Admins",
            "requested_group_description": "desc",
            "requested_app_id": app_obj.id,
            "requested_plugin_data": {DummyPlugin.ID: {"configuration": {}}},
        },
    )
    assert resp.status_code == 400
    assert "group_id" in resp.text


async def test_post_app_group_request_accepts_valid_plugin_config(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
) -> None:
    user: OktaUser = await OktaUserFactory.create_async()
    db.session.add(user)
    await db.session.commit()
    mock_user(user)
    app_obj = await _make_app_with_plugin(db)

    payload = {DummyPlugin.ID: {"configuration": {"group_id": "g-77"}}}
    resp = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_type": "app_group",
            "requested_group_name": f"App-{app_obj.name}-Admins",
            "requested_group_description": "desc",
            "requested_app_id": app_obj.id,
            "requested_plugin_data": payload,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["requested_plugin_data"] == payload


# --- An unregistered plugin id is a 400, not a 500 -------------------------
#
# AppGroupLifecyclePluginFilteringError subclasses Exception, not ValueError, so
# each of the three plugin-config failure paths below has to name it explicitly or
# the request 500s on what is really bad operator/approver input.


async def test_post_app_group_request_returns_400_when_app_plugin_is_unregistered(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
) -> None:
    """Submit-time validation against an app whose plugin id isn't registered."""
    user: OktaUser = await OktaUserFactory.create_async()
    db.session.add(user)
    await db.session.commit()
    mock_user(user)
    app_obj = await _make_app_with_unregistered_plugin(db)

    resp = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_type": "app_group",
            "requested_group_name": f"App-{app_obj.name}-Admins",
            "requested_group_description": "desc",
            "requested_app_id": app_obj.id,
            "requested_plugin_data": {UNREGISTERED_PLUGIN_ID: {"configuration": {"group_id": "g-1"}}},
        },
    )
    assert resp.status_code == 400, resp.text
    assert UNREGISTERED_PLUGIN_ID in resp.text


async def test_post_auto_approve_unregistered_plugin_returns_400(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
    mocker: MockerFixture,
) -> None:
    """Same error raised from the auto-approve inside CreateGroupRequest (an app
    owner's own request). Submit-time validation is forced to pass so the failure
    can only be caught by the POST handler's wrapper around the operation."""
    app_obj = await _make_app_with_plugin(db)
    owner = await _make_app_owner(db, app_obj)
    mock_user(owner)

    mocker.patch(
        "api.plugins.app_group_lifecycle.validate_app_group_lifecycle_plugin_group_config",
        side_effect=[{}, AppGroupLifecyclePluginFilteringError(DummyPlugin.ID, 0)],
    )

    resp = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_type": "app_group",
            "requested_group_name": f"App-{app_obj.name}-Admins",
            "requested_group_description": "desc",
            "requested_app_id": app_obj.id,
            "requested_plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g-1"}}},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "Expected one response" in resp.text
    gr = (await db.session.scalars(select(GroupRequest))).first()
    assert gr is not None
    assert gr.status == AccessRequestStatus.PENDING


async def test_put_approve_returns_400_when_app_plugin_is_unregistered(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
) -> None:
    """Same error raised from approval-time re-validation on the resolve path."""
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    await db.session.commit()
    app_obj = await _make_app_with_unregistered_plugin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={UNREGISTERED_PLUGIN_ID: {"configuration": {"group_id": "g-1"}}},
    ).execute()
    assert gr is not None
    # Capture the id up front: the failed request rolls its session back, which
    # expires every instance in the identity map, and touching `gr.id` afterwards
    # would lazy-load outside a greenlet (MissingGreenlet).
    gr_id = gr.id

    mock_user(admin)
    resp = await client.put(
        url_for("api-group-requests.group_request_by_id_put", group_request_id=gr_id),
        json={"approved": True},
    )
    assert resp.status_code == 400, resp.text
    assert UNREGISTERED_PLUGIN_ID in resp.text
    refreshed = await db.session.get(GroupRequest, gr_id)
    assert refreshed is not None
    assert refreshed.status == AccessRequestStatus.PENDING
    assert refreshed.approved_group_id is None


# --- Approval applies plugin config ----------------------------------------


async def test_approve_applies_resolved_plugin_data_over_requested(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    test_plugin: DummyPlugin,
    mocker: MockerFixture,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: OktaSdkGroup.from_dict({"id": "createdgrp0000000001"})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    await db.session.commit()
    app_obj = await _make_app_with_plugin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "from-request"}}},
    ).execute()
    assert gr is not None

    gr.resolved_plugin_data = {DummyPlugin.ID: {"configuration": {"group_id": "from-resolver"}}}
    await db.session.commit()

    await ApproveGroupRequest(group_request=gr, approver_user=admin, approval_reason="ok").execute()

    created = await db.session.get(AppGroup, "createdgrp0000000001")
    assert created is not None
    assert created.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "from-resolver"
    assert "createdgrp0000000001" in test_plugin.group_created_calls


async def test_approve_falls_back_to_requested_plugin_data(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    test_plugin: DummyPlugin,
    mocker: MockerFixture,
) -> None:
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: OktaSdkGroup.from_dict({"id": "createdgrp0000000002"})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    await db.session.commit()
    app_obj = await _make_app_with_plugin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "only-requested"}}},
    ).execute()
    assert gr is not None

    await ApproveGroupRequest(group_request=gr, approver_user=admin, approval_reason="ok").execute()

    created = await db.session.get(AppGroup, "createdgrp0000000002")
    assert created is not None
    assert created.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "only-requested"


# --- PUT resolve carries resolved_plugin_data through approval --------------


async def test_put_group_request_persists_resolved_plugin_data(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: OktaSdkGroup.from_dict({"id": "createdgrp0000000003"})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    # The seeded admin (email == CURRENT_OKTA_USER_EMAIL) is in access owners, so it
    # passes both the router's is_access_admin check and the operation's own
    # get_access_owners authorization without further mocking.
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    await db.session.commit()
    app_obj = await _make_app_with_plugin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "req"}}},
    ).execute()
    assert gr is not None

    mock_user(admin)
    resp = await client.put(
        url_for("api-group-requests.group_request_by_id_put", group_request_id=gr.id),
        json={
            "approved": True,
            "resolved_plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "resolved"}}},
        },
    )
    assert resp.status_code == 200, resp.text
    created = await db.session.get(AppGroup, "createdgrp0000000003")
    assert created is not None
    assert created.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "resolved"


# --- POST surfaces an approval-time plugin error as 400 (not 500) ----------


async def test_post_auto_approve_invalid_plugin_config_returns_400(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
    mocker: MockerFixture,
) -> None:
    """An app-owner's own app-group request auto-approves inside the create
    operation. If the plugin config is rejected at that approval step, the POST
    must return a clean 400 (like the PUT/approve path) rather than a 500 from
    an uncaught ValueError. We force the (defensive) approval-time validation to
    fail while letting the router's submit-time validation pass, so the failure
    can only be caught by the POST handler's ValueError wrapper."""
    app_obj = await _make_app_with_plugin(db)
    owner = await _make_app_owner(db, app_obj)
    mock_user(owner)

    # First call = router submit-time validation (passes); second = approval-time
    # re-validation inside ApproveGroupRequest (fails).
    mocker.patch(
        "api.plugins.app_group_lifecycle.validate_app_group_lifecycle_plugin_group_config",
        side_effect=[{}, {"group_id": "changed since submit"}],
    )

    resp = await client.post(
        url_for("api-group-requests.group_requests_create"),
        json={
            "requested_group_type": "app_group",
            "requested_group_name": f"App-{app_obj.name}-Admins",
            "requested_group_description": "desc",
            "requested_app_id": app_obj.id,
            "requested_plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g-1"}}},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "group_id" in resp.text
    # The request must not have been approved / created despite the auto-approve attempt.
    gr = (await db.session.scalars(select(GroupRequest))).first()
    assert gr is not None
    assert gr.status == AccessRequestStatus.PENDING


# --- Approval deep-copies plugin config off the request record -------------


async def test_approve_does_not_mutate_request_plugin_data(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mutating_plugin: _StatusWritingPlugin,
    mocker: MockerFixture,
) -> None:
    """The group_created hook may mutate the created group's plugin_data (e.g.
    writing status). That must not leak back into the request's stored
    requested_plugin_data, which is immutable history — ApproveGroupRequest
    deep-copies before handing the config to the new group."""
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: OktaSdkGroup.from_dict({"id": "createdgrp0000000004"})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    await db.session.commit()
    app_obj = await _make_app_with_plugin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "only-requested"}}},
    ).execute()
    assert gr is not None

    await ApproveGroupRequest(group_request=gr, approver_user=admin, approval_reason="ok").execute()

    created = await db.session.get(AppGroup, "createdgrp0000000004")
    assert created is not None
    # The hook mutated the created group's copy...
    assert created.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "mutated-by-hook"
    assert "createdgrp0000000004" in mutating_plugin.group_created_calls
    # ...but the request's stored config is untouched.
    assert gr.requested_plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "only-requested"


# --- A failed approval leaves no approve audit event ------------------------


async def test_approve_invalid_plugin_config_emits_no_approve_audit_event(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    test_plugin: DummyPlugin,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approval-time re-validation of the plugin config runs with the rest of the
    validation, *before* the group_request_approve audit event is emitted. So a
    config that has gone invalid since filing must leave no "approved" audit entry
    behind for an approval that never completed — the router turns the failure into
    a retryable 400, which would re-emit the event on every retry."""
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    # Mocked so that a regression (validating after the audit log again) fails on the
    # audit assertion below rather than on an unmocked Okta call.
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: OktaSdkGroup.from_dict({"id": "createdgrp0000000006"})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    await db.session.commit()
    app_obj = await _make_app_with_plugin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "valid-at-filing"}}},
    ).execute()
    assert gr is not None
    gr_id = gr.id

    # The plugin now rejects the stored config, as if the plugin's schema changed
    # between filing and approval.
    mocker.patch(
        "api.plugins.app_group_lifecycle.validate_app_group_lifecycle_plugin_group_config",
        return_value={"group_id": "changed since submit"},
    )

    with caplog.at_level(logging.INFO, logger="access.audit"):
        with pytest.raises(ValueError, match="group_id"):
            await ApproveGroupRequest(group_request=gr, approver_user=admin, approval_reason="ok").execute()

    audit_messages = [r.getMessage() for r in caplog.records if r.name == "access.audit"]
    assert not any(EventType.group_request_approve.value in message for message in audit_messages), (
        f"approval failed but emitted a {EventType.group_request_approve.value} audit event: {audit_messages}"
    )
    # The group must not have been created, nor the request resolved.
    assert await db.session.get(AppGroup, "createdgrp0000000006") is None
    refreshed = await db.session.get(GroupRequest, gr_id)
    assert refreshed is not None
    assert refreshed.status == AccessRequestStatus.PENDING
    assert refreshed.approved_group_id is None


# --- Config supplied for an app with no plugin is dropped with a warning ----


async def test_approve_drops_plugin_data_and_warns_when_app_has_no_plugin(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If a request carries plugin config but the (resolved) app has no
    lifecycle plugin, the config can't be applied. Approval drops it and logs a
    warning rather than silently discarding it."""
    admin = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: OktaSdkGroup.from_dict({"id": "createdgrp0000000005"})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    requester: OktaUser = await OktaUserFactory.create_async()
    db.session.add(requester)
    app_obj = await AppFactory.create_async()  # no app_group_lifecycle_plugin configured
    db.session.add(app_obj)
    await db.session.commit()

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app_obj.name}-Admins",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "orphaned"}}},
    ).execute()
    assert gr is not None

    with caplog.at_level(logging.WARNING, logger="api.operations.approve_group_request"):
        await ApproveGroupRequest(group_request=gr, approver_user=admin, approval_reason="ok").execute()

    created = await db.session.get(AppGroup, "createdgrp0000000005")
    assert created is not None
    assert created.plugin_data == {}
    assert any("no app_group_lifecycle_plugin" in r.getMessage() for r in caplog.records)
