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
