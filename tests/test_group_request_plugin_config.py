from typing import Any, Callable, Generator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import TypeAdapter
from pytest_mock import MockerFixture
from sqlalchemy import select

from okta.models import Group as OktaSdkGroup

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, AppGroup, GroupRequest, OktaUser
from api.operations import ApproveGroupRequest, CreateGroupRequest
from api.schemas.requests_schemas import (
    CreateGroupRequestBody,
    ResolveGroupRequestBody,
)
from api.services import okta
from tests.factories import AppFactory, OktaUserFactory
from tests.test_app_group_lifecycle_plugin import DummyPlugin


@pytest.fixture
def test_plugin(app: FastAPI, mocker: MockerFixture) -> Generator[DummyPlugin, None, None]:
    """Register DummyPlugin (id 'test_plugin') as the app group lifecycle plugin."""
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module
    from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginSpec

    instance = DummyPlugin()
    pm = pluggy.PluginManager("access_app_group_lifecycle")
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)
    pm.register(instance, name=DummyPlugin.ID)
    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    yield instance
    plugin_module._cached_app_group_lifecycle_hook = None


async def test_group_request_plugin_data_defaults_to_empty_dict(app: FastAPI, db: Db) -> None:
    user: OktaUser = OktaUserFactory.build()
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
    user: OktaUser = OktaUserFactory.build()
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


def test_app_group_body_accepts_requested_plugin_data() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(CreateGroupRequestBody)
    body = adapter.validate_python(
        {
            "requested_group_type": "app_group",
            "requested_group_name": "App-Foo-Admins",
            "requested_app_id": "app00000000000000001",
            "requested_group_description": "Test description",
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
            "requested_app_id": "app00000000000000001",
            "requested_group_description": "Test description",
        }
    )
    assert body.requested_plugin_data == {}


def test_resolve_body_accepts_resolved_plugin_data() -> None:
    body = ResolveGroupRequestBody.model_validate(
        {"approved": True, "resolved_plugin_data": {"test_plugin": {"configuration": {"group_id": "g-2"}}}}
    )
    assert body.resolved_plugin_data == {"test_plugin": {"configuration": {"group_id": "g-2"}}}


async def test_create_group_request_persists_requested_plugin_data(app: FastAPI, db: Db) -> None:
    user: OktaUser = OktaUserFactory.build()
    db.session.add(user)
    await db.session.commit()
    app_obj = AppFactory.build()
    db.session.add(app_obj)
    await db.session.commit()

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


async def _make_app_with_plugin(db: Db) -> Any:
    app_obj = AppFactory.build()
    app_obj.app_group_lifecycle_plugin = DummyPlugin.ID
    db.session.add(app_obj)
    await db.session.commit()
    return app_obj


async def test_post_app_group_request_rejects_missing_required_plugin_config(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mock_user: Callable[[Any], None],
    url_for: Callable[..., str],
    test_plugin: DummyPlugin,
) -> None:
    user: OktaUser = OktaUserFactory.build()
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
    user: OktaUser = OktaUserFactory.build()
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

    requester: OktaUser = OktaUserFactory.build()
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

    requester: OktaUser = OktaUserFactory.build()
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
    requester: OktaUser = OktaUserFactory.build()
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
