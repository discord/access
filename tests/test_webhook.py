import json

from flask import Flask, url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from api.views.resources.webhook import OKTA_WEBHOOK_VERIFICATION_HEADER_NAME


def test_get_okta_webhook(app: Flask, client: FlaskClient, db: SQLAlchemy) -> None:
    webhook_url = url_for("api-webhooks.okta_webhook")

    rep = client.get(webhook_url)
    assert rep.status_code == 403

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()
    app.config["OKTA_WEBHOOK_ID"] = access_owner.id

    rep = client.get(webhook_url)
    assert rep.status_code == 400

    rep = client.get(webhook_url, headers={OKTA_WEBHOOK_VERIFICATION_HEADER_NAME: "test"})
    assert rep.status_code == 200
    results = rep.get_json()
    results["verification"] == "test"


TEST_OKTA_WEBHOOK_USER_MODIFY_EVENT = """
{{
    "eventType": "com.okta.event_hook",
    "eventTypeVersion": "1.0",
    "cloudEventsVersion": "0.1",
    "source": "https://sandbox.oktapreview.com/api/v1/eventHooks/who88iadjbnbLhqF01d7",
    "eventId": "7725f197-576a-4eff-b1ce-668221e8144a",
    "data": {{
        "events": [
            {{
                "uuid": "6c5775f9-f9a3-11ed-8d5b-c305421bade6",
                "published": "2023-05-23T19:53:01.101Z",
                "eventType": "{event_type}",
                "version": "0",
                "displayMessage": "Remove user from group membership",
                "severity": "INFO",
                "client": {{
                    "userAgent": {{
                        "rawUserAgent": "okta_iga_connector",
                        "os": "Unknown",
                        "browser": "UNKNOWN"
                    }},
                    "zone": "null",
                    "device": "Unknown",
                    "id": null,
                    "ipAddress": "0.0.0.0",
                    "geographicalContext": {{
                        "city": "Boardman",
                        "state": "Oregon",
                        "country": "United States",
                        "postalCode": "97818",
                        "geolocation": {{
                            "lat": 45.8234,
                            "lon": -119.7257
                        }}
                    }},
                    "ipChain": [
                        {{
                            "ip": "0.0.0.0",
                            "geographicalContext": {{
                                "city": "Boardman",
                                "state": "Oregon",
                                "country": "United States",
                                "postalCode": "97818",
                                "geolocation": {{
                                    "lat": 45.8234,
                                    "lon": -119.7257
                                }}
                            }},
                            "version": "V4",
                            "source": null
                        }}
                    ]
                }},
                "device": null,
                "actor": {{
                    "id": "{actor_id}",
                    "type": "PublicClientAppEntity",
                    "alternateId": "unknown",
                    "displayName": "Okta IGA Connector",
                    "detailEntry": null
                }},
                "outcome": {{
                    "result": "SUCCESS",
                    "reason": null
                }},
                "target": [
                    {{
                        "id": "{user_id}",
                        "type": "User",
                        "alternateId": "wumpus@discordapp.com",
                        "displayName": "Wumpus",
                        "detailEntry": null
                    }},
                    {{
                        "id": "{group_id}",
                        "type": "UserGroup",
                        "alternateId": "unknown",
                        "displayName": "Group",
                        "detailEntry": null
                    }}
                ],
                "transaction": {{
                    "type": "WEB",
                    "id": "",
                    "detail": {{
                        "requestApiTokenId": ""
                    }}
                }}
            }}
        ]
    }},
    "eventTime": "2023-05-23T20:33:08.822Z",
    "contentType": "application/json"
}}
"""


def test_post_okta_webhook(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    user: OktaUser,
    okta_group: OktaGroup,
    role_group: RoleGroup,
) -> None:
    webhook_url = url_for("api-webhooks.okta_webhook")

    rep = client.post(webhook_url)
    assert rep.status_code == 403

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()
    app.config["OKTA_WEBHOOK_ID"] = access_owner.id

    rep = client.post(webhook_url)
    assert rep.status_code == 400

    app.config["OKTA_IGA_ACTOR_ID"] = access_owner.id

    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")

    rep = client.post(
        webhook_url,
        json=json.loads(
            TEST_OKTA_WEBHOOK_USER_MODIFY_EVENT.format(
                event_type="group.user_membership.add",
                actor_id=access_owner.id,
                user_id=user.id,
                group_id=okta_group.id,
            )
        ),
    )
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 2

    ModifyRoleGroups(role_group=role_group, groups_to_add=[okta_group.id]).execute()
    ModifyGroupUsers(group=role_group, members_to_add=[user.id]).execute()

    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()

    rep = client.post(
        webhook_url,
        json=json.loads(
            TEST_OKTA_WEBHOOK_USER_MODIFY_EVENT.format(
                event_type="group.user_membership.remove",
                actor_id=access_owner.id,
                user_id=user.id,
                group_id=okta_group.id,
            )
        ),
    )
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 2
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 1
