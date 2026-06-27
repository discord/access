from fastapi import FastAPI

from api.extensions import Db
from api.models import AccessRequestStatus, GroupRequest, OktaUser
from tests.factories import OktaUserFactory


def test_group_request_plugin_data_defaults_to_empty_dict(app: FastAPI, db: Db) -> None:
    user: OktaUser = OktaUserFactory.create()
    db.session.add(user)
    db.session.commit()

    gr = GroupRequest(
        id="reqplugindata0000001",
        status=AccessRequestStatus.PENDING,
        requester_user_id=user.id,
        requested_group_name="Test Group",
        requested_group_type="okta_group",
    )
    db.session.add(gr)
    db.session.commit()
    db.session.refresh(gr)

    assert gr.requested_plugin_data == {}
    assert gr.resolved_plugin_data == {}


def test_group_request_plugin_data_round_trips(app: FastAPI, db: Db) -> None:
    user: OktaUser = OktaUserFactory.create()
    db.session.add(user)
    db.session.commit()

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
    db.session.commit()
    db.session.refresh(gr)

    assert gr.requested_plugin_data == payload
    assert gr.resolved_plugin_data == {}


from api.schemas.requests_schemas import (
    CreateGroupRequestBody,
    ResolveGroupRequestBody,
)
from pydantic import TypeAdapter


def test_app_group_body_accepts_requested_plugin_data() -> None:
    adapter = TypeAdapter(CreateGroupRequestBody)
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
    adapter = TypeAdapter(CreateGroupRequestBody)
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
