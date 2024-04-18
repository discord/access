from datetime import datetime, timedelta
from typing import Any

from flask import Flask, url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.operations import (
    ApproveAccessRequest,
    CreateAccessRequest,
    ModifyGroupUsers,
    ModifyRoleGroups,
    RejectAccessRequest,
)
from api.plugins import ConditionalAccessResponse, get_conditional_access_hook, get_notification_hook
from api.services import okta
from tests.factories import AccessRequestFactory, AppGroupFactory, OktaUserFactory

SEVEN_DAYS_IN_SECONDS = 7 * 24 * 60 * 60
THREE_DAYS_IN_SECONDS = 3 * 24 * 60 * 60
ONE_DAY_IN_SECONDS = 24 * 60 * 60

def test_get_access_request(app: Flask, client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # test 404
    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id="randomid"
    )
    rep = client.get(access_request_url)
    assert rep.status_code == 404

    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()

    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[okta_group.id],
        owner_groups_to_add=[okta_group.id],
        sync_to_okta=False
    ).execute()

    okta_group_access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    assert okta_group_access_request is not None

    role_group_access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    assert role_group_access_request is not None

    # test get okta group access_request
    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=okta_group_access_request.id
    )
    rep = client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == okta_group_access_request.status
    assert data["request_reason"] == okta_group_access_request.request_reason
    assert data["request_ownership"] == okta_group_access_request.request_ownership

    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    okta_group_access_request = ApproveAccessRequest(
        access_request=okta_group_access_request, approver_user=access_owner
    ).execute()

    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0

    rep = client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == okta_group_access_request.status
    assert data["request_reason"] == okta_group_access_request.request_reason
    assert data["request_ownership"] == okta_group_access_request.request_ownership

    add_user_to_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()

    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=role_group_access_request.id
    )
    rep = client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == role_group.name
    assert data["status"] == role_group_access_request.status
    assert data["request_reason"] == role_group_access_request.request_reason
    assert data["request_ownership"] == role_group_access_request.request_ownership

    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    role_group_access_request = ApproveAccessRequest(
        access_request=role_group_access_request, approver_user=access_owner
    ).execute()

    assert add_user_to_group_spy.call_count == 2
    assert add_owner_to_group_spy.call_count == 1

    rep = client.get(access_request_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == role_group.name
    assert data["status"] == role_group_access_request.status
    assert data["request_reason"] == role_group_access_request.request_reason
    assert data["request_ownership"] == role_group_access_request.request_ownership


def test_put_access_request(app: Flask, client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser) -> None:
    # test 404
    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id="randomid"
    )
    rep = client.put(access_request_url)
    assert rep.status_code == 404

    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    db.session.commit()

    # test missing data
    data: dict[str, Any] = {}
    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=access_request.id
    )
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 400

    # test update access_request
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    data = {"approved": True, "reason": "test reason"}

    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0

    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert access_request.status == AccessRequestStatus.APPROVED
    assert data["status"] == access_request.status
    assert data["request_reason"] == access_request.request_reason
    assert data["request_ownership"] == access_request.request_ownership
    assert data["resolver"]["email"] == access_owner.email
    assert data["resolution_reason"] == access_request.resolution_reason

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )

    access_request2 = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()
    assert access_request2 is not None
    access_request = access_request2

    add_user_to_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    data = {"approved": False, "reason": "test reason"}

    access_request_url = url_for(
        "api-access-requests.access_request_by_id", access_request_id=access_request.id
    )
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0

    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert access_request.status == AccessRequestStatus.REJECTED
    assert data["status"] == access_request.status
    assert data["request_reason"] == access_request.request_reason
    assert data["request_ownership"] == access_request.request_ownership
    assert data["resolver"]["email"] == access_owner.email
    assert data["resolution_reason"] == access_request.resolution_reason

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )


def test_create_access_request(app: Flask, client: FlaskClient, db: SQLAlchemy, okta_group: OktaGroup) -> None:
    # test bad data
    access_requests_url = url_for("api-access-requests.access_requests")
    data: dict[str, Any]  = {}
    rep = client.post(access_requests_url, json=data)
    assert rep.status_code == 400

    db.session.add(okta_group)
    db.session.commit()

    data = {
        "group_id": okta_group.id,
        "group_owner": False,
        "reason": "test reason",
    }

    rep = client.post(access_requests_url, json=data)
    assert rep.status_code == 201

    data = rep.get_json()
    access_request = db.session.get(AccessRequest, data["id"])
    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    assert data["requester"]["email"] == access_owner.email
    assert data["requested_group"]["name"] == okta_group.name
    assert access_request.status == AccessRequestStatus.PENDING
    assert data["status"] == access_request.status
    assert data["request_reason"] == access_request.request_reason
    assert data["request_ownership"] == access_request.request_ownership


def test_get_all_access_request(client: FlaskClient, db: SQLAlchemy, okta_group: OktaGroup, user: OktaUser) -> None:
    access_requests_url = url_for("api-access-requests.access_requests")
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()

    access_requests = AccessRequestFactory.create_batch(
        10, requester_user_id=user.id, requested_group_id=okta_group.id
    )
    db.session.add_all(access_requests)
    db.session.commit()

    rep = client.get(access_requests_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for access_request in access_requests:
        assert any(u["id"] == access_request.id for u in results["results"])

    rep = client.get(access_requests_url, query_string={"q": "pend"})
    assert rep.status_code == 200

    results = rep.get_json()
    for access_request in access_requests:
        assert any(u["id"] == access_request.id for u in results["results"])


def test_create_access_request_notification(app: Flask, db: SQLAlchemy, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(
        hook, "access_request_created"
    )
    request_completed_notification_spy = mocker.patch.object(
        hook, "access_request_completed"
    )
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert request_created_notification_spy.call_count == 1

    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    ApproveAccessRequest(
        access_request=access_request, approver_user=access_owner
    ).execute()

    assert add_membership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1

    access_request.status = AccessRequestStatus.PENDING
    access_request.resolved_at = None
    db.session.commit()

    add_membership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    RejectAccessRequest(
        access_request=access_request, current_user_id=access_owner
    ).execute()

    assert add_membership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1


def test_create_app_access_request_notification(
    app: Flask, db: SQLAlchemy, access_app: App, app_group: AppGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    # test bad data
    app_owner_user = OktaUserFactory.create()
    app_owner_group = AppGroupFactory.create()

    # Add App
    db.session.add(access_app)

    # Add Users
    db.session.add(app_owner_user)
    db.session.add(user)

    db.session.commit()

    # Add app group that no one owns
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    # Add app owners group
    app_owner_group.app_id = access_app.id
    app_owner_group.is_owner = True
    db.session.add(app_owner_group)

    db.session.commit()

    # Add app_owner_user to the owner group
    ModifyGroupUsers(
        group=app_owner_group,
        members_to_add=[],
        owners_to_add=[app_owner_user.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(
        hook, "access_request_created"
    )
    request_completed_notification_spy = mocker.patch.object(
        hook, "access_request_completed"
    )
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert request_created_notification_spy.call_count == 1

    access_owner = OktaUser.query.filter(
        OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]
    ).first()

    ApproveAccessRequest(
        access_request=access_request, approver_user=access_owner
    ).execute()

    assert add_membership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1

    access_request.status = AccessRequestStatus.PENDING
    access_request.resolved_at = None
    db.session.commit()

    add_membership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    RejectAccessRequest(
        access_request=access_request, current_user_id=access_owner
    ).execute()

    assert add_membership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1

def test_get_all_possible_request_approvers(app: Flask, mocker: MockerFixture, db: SQLAlchemy) -> None:
    access_admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    users = OktaUserFactory.build_batch(3)
    db.session.add_all(users)
    db.session.commit()

    mocker.patch(
        "api.models.access_request.get_group_managers",
        return_value=[users[0], users[1]],
    )

    mocker.patch(
        "api.models.access_request.get_app_managers",
        return_value=[users[0], users[2]],
    )

    req = AccessRequest()
    req.requested_group = AppGroupFactory.create()

    approvers = get_all_possible_request_approvers(req)

    # Assert that the access admin and 3 users are returned with no duplicates
    assert len(approvers) == 4
    assert access_admin in approvers
    assert users[0] in approvers
    assert users[1] in approvers
    assert users[2] in approvers

def test_resolve_app_access_request_notification(
    app: Flask, db: SQLAlchemy, access_app: App, app_group: AppGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    access_admin = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    app_owner_user1 = OktaUserFactory.build()
    app_owner_user2 = OktaUserFactory.build()
    app_owner_group = AppGroupFactory.build()

    # Add App
    db.session.add(access_app)

    # Add Users
    db.session.add(app_owner_user1)
    db.session.add(app_owner_user2)
    db.session.add(user) # Future group owner

    db.session.commit()

    # Add app group that no one owns
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    # Add app owners group
    app_owner_group.app_id = access_app.id
    app_owner_group.is_owner = True
    db.session.add(app_owner_group)

    db.session.commit()

    # Add app_owner_user to the owner group
    ModifyGroupUsers(
        group=app_owner_group,
        members_to_add=[],
        owners_to_add=[app_owner_user1.id, app_owner_user2.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(
        hook, "access_request_created"
    )
    request_completed_notification_spy = mocker.patch.object(
        hook, "access_request_completed"
    )
    add_ownership_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert len(kwargs['approvers']) == 2
    assert app_owner_user1 in kwargs['approvers']
    assert app_owner_user2 in kwargs['approvers']

    ApproveAccessRequest(
        access_request=access_request, approver_user=app_owner_user1
    ).execute()

    assert add_ownership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert len(kwargs['approvers']) == 4
    assert access_admin in kwargs['approvers']
    assert app_owner_user1 in kwargs['approvers']
    assert app_owner_user2 in kwargs['approvers']
    assert user in kwargs['approvers']

    # Reset the access request so we can test the reject path
    access_request.status = AccessRequestStatus.PENDING
    access_request.resolved_at = None
    db.session.commit()

    add_ownership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    RejectAccessRequest(
        access_request=access_request, current_user_id=app_owner_user1
    ).execute()

    assert add_ownership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert len(kwargs['approvers']) == 4
    assert access_admin in kwargs['approvers']
    assert app_owner_user1 in kwargs['approvers']
    assert app_owner_user2 in kwargs['approvers']
    assert user in kwargs['approvers']

def test_auto_resolve_create_access_request(app: Flask,
                                            db: SQLAlchemy,
                                            okta_group: OktaGroup,
                                            user: OktaUser,
                                            tag: Tag,
                                            mocker: MockerFixture) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    notification_hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(
        notification_hook, "access_request_created"
    )
    request_completed_notification_spy = mocker.patch.object(
        notification_hook, "access_request_completed"
    )
    request_hook = get_conditional_access_hook()
    request_created_conditional_access_spy = mocker.patch.object(
        request_hook, "access_request_created", return_value=[
            ConditionalAccessResponse(approved=True,reason="Auto-Approved")]
    )
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert access_request.status == AccessRequestStatus.APPROVED
    assert access_request.resolved_at is not None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == "Auto-Approved"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 1

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs['access_request']
    assert okta_group == kwargs['group']
    assert user == kwargs['requester']
    assert len(kwargs['group_tags']) == 1
    assert tag in kwargs['group_tags']

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()
    request_created_conditional_access_spy.reset_mock()
    add_membership_spy.reset_mock()

    request_created_conditional_access_spy = mocker.patch.object(
        request_hook, "access_request_created", return_value=[
            ConditionalAccessResponse(approved=False, reason="Auto-Rejected")]
    )

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == "Auto-Rejected"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 0

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs['access_request']
    assert okta_group == kwargs['group']
    assert user == kwargs['requester']
    assert len(kwargs['group_tags']) == 1
    assert tag in kwargs['group_tags']

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()
    request_created_conditional_access_spy.reset_mock()
    add_membership_spy.reset_mock()

    request_created_conditional_access_spy = mocker.patch.object(
        request_hook, "access_request_created", return_value=[None]
    )

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert access_request.status == AccessRequestStatus.PENDING
    assert access_request.resolved_at is None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == ''
    assert request_created_notification_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 0

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs['access_request']
    assert okta_group == kwargs['group']
    assert user == kwargs['requester']
    assert len(kwargs['group_tags']) == 1
    assert tag in kwargs['group_tags']

def test_auto_resolve_create_access_request_with_time_limit_constraint_tag(
        app: Flask,
        db: SQLAlchemy,
        okta_group: OktaGroup,
        user: OktaUser,
        tag: Tag,
        mocker: MockerFixture) -> None:

    db.session.add(user)
    db.session.add(okta_group)
    tag.constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS
    }
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    notification_hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(
        notification_hook, "access_request_created"
    )
    request_completed_notification_spy = mocker.patch.object(
        notification_hook, "access_request_completed"
    )
    request_hook = get_conditional_access_hook()
    request_created_conditional_access_spy = mocker.patch.object(
        request_hook, "access_request_created", return_value=[
            ConditionalAccessResponse(
                approved=True,
                reason="Auto-Approved",
                ending_at=datetime.now() + timedelta(seconds=SEVEN_DAYS_IN_SECONDS)
            ),
        ]
    )
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    assert access_request.status == AccessRequestStatus.APPROVED
    assert access_request.resolved_at is not None
    assert access_request.resolver_user_id is None
    assert access_request.resolution_reason == "Auto-Approved"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 1

    _, kwargs = request_created_conditional_access_spy.call_args
    assert access_request == kwargs['access_request']
    assert okta_group == kwargs['group']
    assert user == kwargs['requester']
    assert len(kwargs['group_tags']) == 1
    assert tag in kwargs['group_tags']