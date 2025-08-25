from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask, url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.operations import (
    ApproveRoleRequest,
    CreateRoleRequest,
    ModifyGroupUsers,
    ModifyRoleGroups,
    RejectRoleRequest,
)
from api.plugins import ConditionalAccessResponse, get_conditional_access_hook, get_notification_hook
from api.services import okta
from tests.factories import AppGroupFactory, OktaGroupFactory, OktaUserFactory, RoleGroupFactory, RoleRequestFactory

SEVEN_DAYS_IN_SECONDS = 7 * 24 * 60 * 60
THREE_DAYS_IN_SECONDS = 3 * 24 * 60 * 60
ONE_DAY_IN_SECONDS = 24 * 60 * 60


def test_get_role_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
) -> None:
    # test 404
    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id="randomid")
    rep = client.get(role_request_url)
    assert rep.status_code == 404

    role_group2 = RoleGroupFactory.create()

    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(role_group2)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    # should be OK
    okta_group_role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    assert okta_group_role_request is not None

    # should not be allowed
    role_group_role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=role_group2,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    assert role_group_role_request is None

    # test get okta group role_request
    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id=okta_group_role_request.id)
    rep = client.get(role_request_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requester_role"]["name"] == role_group.name
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == okta_group_role_request.status
    assert data["request_reason"] == okta_group_role_request.request_reason
    assert data["request_ownership"] == okta_group_role_request.request_ownership

    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.get(role_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 0
    assert len(data["groups_owned_by_role"]) == 0

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    okta_group_role_request = ApproveRoleRequest(
        role_request=okta_group_role_request, approver_user=access_owner
    ).execute()

    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0

    rep = client.get(role_request_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == okta_group_role_request.status
    assert data["request_reason"] == okta_group_role_request.request_reason
    assert data["request_ownership"] == okta_group_role_request.request_ownership

    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.get(role_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 0


def test_put_role_request(
    app: Flask,
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    role_request: RoleRequest,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
) -> None:
    # test 404
    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id="randomid")
    rep = client.put(role_request_url)
    assert rep.status_code == 404

    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    role_request.requested_group_id = okta_group.id
    role_request.requester_role_id = role_group.id
    role_request.requester_user_id = user.id
    db.session.add(role_request)
    db.session.commit()

    # test missing data
    data: dict[str, Any] = {}
    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id=role_request.id)
    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 400

    # test update role_request
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    assert RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count() == 0
    # The Access owner plus the role membership and ownership added above
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 3

    data = {"approved": True, "reason": "test reason"}

    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requester_role"]["name"] == role_group.name
    assert data["requested_group"]["name"] == okta_group.name
    assert role_request.status == AccessRequestStatus.APPROVED
    assert data["status"] == role_request.status
    assert data["request_reason"] == role_request.request_reason
    assert data["request_ownership"] == role_request.request_ownership
    assert data["resolver"]["email"] == access_owner.email
    assert data["resolution_reason"] == role_request.resolution_reason

    assert RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count() == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    role_request2 = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()
    assert role_request2 is not None
    role_request = role_request2

    add_user_to_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    data = {"approved": False, "reason": "test reason"}

    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id=role_request.id)
    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requester_role"]["name"] == role_group.name
    assert data["requested_group"]["name"] == okta_group.name
    assert role_request.status == AccessRequestStatus.REJECTED
    assert data["status"] == role_request.status
    assert data["request_reason"] == role_request.request_reason
    assert data["request_ownership"] == role_request.request_ownership
    assert data["resolver"]["email"] == access_owner.email
    assert data["resolution_reason"] == role_request.resolution_reason

    assert RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count() == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4


def test_put_role_request_by_non_owner(
    client: FlaskClient, app: Flask, db: SQLAlchemy, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.commit()

    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[access_owner.id, user.id],
        sync_to_okta=False,
    ).execute()

    role_request_by_owner = CreateRoleRequest(
        requester_user=access_owner,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    role_request_by_non_owner = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    db.session.commit()

    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    assert role_request_by_owner is not None
    assert role_request_by_owner.status == AccessRequestStatus.PENDING
    assert role_request_by_non_owner is not None
    assert role_request_by_non_owner.status == AccessRequestStatus.PENDING

    data: dict[str, Any] = {}
    app.config["CURRENT_OKTA_USER_EMAIL"] = user.email

    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id=role_request_by_owner.id)
    data = {"approved": True, "reason": "test approval"}
    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 403

    assert role_request_by_owner.status == AccessRequestStatus.PENDING
    assert role_request_by_owner.resolved_at is None
    assert role_request_by_owner.resolver_user_id is None

    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    data = {"approved": False, "reason": "test rejection"}

    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 403

    assert role_request_by_owner.status == AccessRequestStatus.PENDING
    assert role_request_by_owner.resolved_at is None
    assert role_request_by_owner.resolver_user_id is None

    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    role_request_url = url_for("api-role-requests.role_request_by_id", role_request_id=role_request_by_non_owner.id)

    data = {"approved": True, "reason": "test approval"}
    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 403

    assert role_request_by_non_owner.status == AccessRequestStatus.PENDING
    assert role_request_by_non_owner.resolved_at is None
    assert role_request_by_non_owner.resolver_user_id is None

    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    data = {"approved": False, "reason": "test rejection"}

    rep = client.put(role_request_url, json=data)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["requester"]["email"] == user.email
    assert data["requester_role"]["name"] == role_group.name
    assert data["requested_group"]["name"] == okta_group.name
    assert data["status"] == AccessRequestStatus.REJECTED
    assert data["request_reason"] == role_request_by_non_owner.request_reason
    assert data["request_ownership"] == role_request_by_non_owner.request_ownership
    assert data["resolver"]["email"] == user.email
    assert data["resolution_reason"] == role_request_by_non_owner.resolution_reason

    assert role_request_by_non_owner.resolved_at is not None
    assert role_request_by_non_owner.resolver_user_id == user.id

    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4


def test_create_role_request(
    app: Flask, client: FlaskClient, db: SQLAlchemy, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    # test bad data
    role_requests_url = url_for("api-role-requests.role_requests")
    data: dict[str, Any] = {}
    rep = client.post(role_requests_url, json=data)
    assert rep.status_code == 400

    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()

    data = {
        "role_id": role_group.id,
        "group_id": okta_group.id,
        "group_owner": False,
        "reason": "test reason",
    }

    rep = client.post(role_requests_url, json=data)
    assert rep.status_code == 201

    data = rep.get_json()
    role_request = db.session.get(RoleRequest, data["id"])
    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    assert data["requester"]["email"] == access_owner.email
    assert data["requester_role"]["name"] == role_group.name
    assert data["requested_group"]["name"] == okta_group.name
    assert role_request.status == AccessRequestStatus.PENDING
    assert data["status"] == role_request.status
    assert data["request_reason"] == role_request.request_reason
    assert data["request_ownership"] == role_request.request_ownership


# Try to create an role request when not the role owner or Access admin, then become owner and try again
def test_create_role_request_not_role_owner(
    app: Flask, client: FlaskClient, db: SQLAlchemy, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()

    app.config["CURRENT_OKTA_USER_EMAIL"] = user.email

    data = {
        "role_id": role_group.id,
        "group_id": okta_group.id,
        "group_owner": False,
        "reason": "test reason",
    }

    role_requests_url = url_for("api-role-requests.role_requests")
    rep = client.post(role_requests_url, json=data)
    assert rep.status_code == 403

    ModifyGroupUsers(
        group=role_group,
        members_to_add=[],
        owners_to_add=[user.id],
        sync_to_okta=False,
    ).execute()

    data = {
        "role_id": role_group.id,
        "group_id": okta_group.id,
        "group_owner": False,
        "reason": "test reason",
    }

    rep = client.post(role_requests_url, json=data)
    assert rep.status_code == 201

    out = rep.get_json()
    role_request = db.session.get(RoleRequest, out["id"])

    assert out["requester"]["email"] == user.email
    assert out["requester_role"]["name"] == role_group.name
    assert out["requested_group"]["name"] == okta_group.name
    assert role_request.status == AccessRequestStatus.PENDING
    assert out["status"] == role_request.status
    assert out["request_reason"] == role_request.request_reason
    assert out["request_ownership"] == role_request.request_ownership


def test_get_all_role_request(
    client: FlaskClient,
    db: SQLAlchemy,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    role_requests_url = url_for("api-role-requests.role_requests")
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.commit()

    role_requests = RoleRequestFactory.create_batch(
        10, requester_user_id=user.id, requester_role_id=role_group.id, requested_group_id=okta_group.id
    )
    db.session.add_all(role_requests)
    db.session.commit()

    rep = client.get(role_requests_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for request in role_requests:
        assert any(u["id"] == request.id for u in results["results"])

    rep = client.get(role_requests_url, query_string={"q": "pend"})
    assert rep.status_code == 200

    results = rep.get_json()
    for request in role_requests:
        assert any(u["id"] == request.id for u in results["results"])

    # Should be able to query by requester role and requested group
    rep = client.get(role_requests_url, query_string={"q": role_requests[0].requester_role.name})
    assert rep.status_code == 200

    results = rep.get_json()
    assert any(u["id"] == role_requests[0].id for u in results["results"])

    rep = client.get(role_requests_url, query_string={"q": role_requests[0].requested_group.name})
    assert rep.status_code == 200

    results = rep.get_json()
    assert any(u["id"] == role_requests[0].id for u in results["results"])


def test_create_role_request_notification(
    app: Flask, db: SQLAlchemy, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_role_request_completed")
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    ApproveRoleRequest(role_request=role_request, approver_user=access_owner).execute()

    assert add_membership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1

    role_request.status = AccessRequestStatus.PENDING
    role_request.resolved_at = None
    db.session.commit()

    add_membership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    RejectRoleRequest(role_request=role_request, current_user_id=access_owner).execute()

    assert add_membership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1


def test_create_app_role_request_notification(
    app: Flask,
    db: SQLAlchemy,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    user: OktaUser,
    mocker: MockerFixture,
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

    # Add role group that user owns
    db.session.add(role_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    # Add app_owner_user to the owner group
    ModifyGroupUsers(
        group=app_owner_group, members_to_add=[], owners_to_add=[app_owner_user.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_role_request_completed")
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    ApproveRoleRequest(role_request=role_request, approver_user=access_owner).execute()

    assert add_membership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user

    role_request.status = AccessRequestStatus.PENDING
    role_request.resolved_at = None
    db.session.commit()

    add_membership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    RejectRoleRequest(role_request=role_request, current_user_id=access_owner).execute()

    assert add_membership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user


def test_get_all_possible_role_request_approvers(app: Flask, mocker: MockerFixture, db: SQLAlchemy) -> None:
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

    req = RoleRequest()
    req.requested_group = AppGroupFactory.create()

    approvers = get_all_possible_request_approvers(req)

    # Assert that the access admin and 3 users are returned with no duplicates
    assert len(approvers) == 4
    assert access_admin in approvers
    assert users[0] in approvers
    assert users[1] in approvers
    assert users[2] in approvers


def test_role_request_approvers_tagged(
    app: Flask,
    db: SQLAlchemy,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    tag: Tag,
    mocker: MockerFixture,
) -> None:
    user2 = OktaUserFactory.create()
    user3 = OktaUserFactory.create()
    db.session.add(user)
    db.session.add(user2)
    db.session.add(user3)
    db.session.add(okta_group)
    db.session.commit()

    # Add role group that user owns
    db.session.add(role_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user2.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=okta_group, owners_to_add=[user2.id, user3.id], sync_to_okta=False).execute()

    # Add tag
    tag.constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
    }
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == okta_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 1
    assert user3 in kwargs["approvers"]


def test_resolve_app_role_request_notification(
    app: Flask,
    db: SQLAlchemy,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    user: OktaUser,
    mocker: MockerFixture,
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
    db.session.add(user)  # Future group owner

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

    # Add role group that user owns
    db.session.add(role_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    # Add app_owner_user to the owner group
    ModifyGroupUsers(
        group=app_owner_group,
        members_to_add=[],
        owners_to_add=[app_owner_user1.id, app_owner_user2.id],
        sync_to_okta=False,
    ).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_role_request_completed")
    add_ownership_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 2
    assert app_owner_user1 in kwargs["approvers"]
    assert app_owner_user2 in kwargs["approvers"]

    ApproveRoleRequest(role_request=role_request, approver_user=app_owner_user1).execute()

    assert add_ownership_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 4
    assert access_admin in kwargs["approvers"]
    assert app_owner_user1 in kwargs["approvers"]
    assert app_owner_user2 in kwargs["approvers"]
    assert user in kwargs["approvers"]

    # Reset the access request so we can test the reject path
    role_request.status = AccessRequestStatus.PENDING
    role_request.resolved_at = None
    db.session.commit()

    add_ownership_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    RejectRoleRequest(role_request=role_request, current_user_id=app_owner_user1).execute()

    assert add_ownership_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 4
    assert access_admin in kwargs["approvers"]
    assert app_owner_user1 in kwargs["approvers"]
    assert app_owner_user2 in kwargs["approvers"]
    assert user in kwargs["approvers"]


def test_auto_resolve_create_role_request(
    app: Flask,
    db: SQLAlchemy,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
    tag: Tag,
    mocker: MockerFixture,
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    notification_hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(notification_hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(notification_hook, "access_role_request_completed")
    request_hook = get_conditional_access_hook()
    request_created_conditional_access_spy = mocker.patch.object(
        request_hook,
        "role_request_created",
        return_value=[ConditionalAccessResponse(approved=True, reason="Auto-Approved")],
    )
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert role_request.status == AccessRequestStatus.APPROVED
    assert role_request.resolved_at is not None
    assert role_request.resolver_user_id is None
    assert role_request.resolution_reason == "Auto-Approved"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 1

    _, kwargs = request_created_conditional_access_spy.call_args
    assert role_request == kwargs["role_request"]
    assert role_group == kwargs["role"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()
    request_created_conditional_access_spy.reset_mock()
    add_membership_spy.reset_mock()

    request_created_conditional_access_spy = mocker.patch.object(
        request_hook,
        "role_request_created",
        return_value=[ConditionalAccessResponse(approved=False, reason="Auto-Rejected")],
    )

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None
    assert role_request.resolver_user_id is None
    assert role_request.resolution_reason == "Auto-Rejected"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 0

    _, kwargs = request_created_conditional_access_spy.call_args
    assert role_request == kwargs["role_request"]
    assert role_group == kwargs["role"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()
    request_created_conditional_access_spy.reset_mock()
    add_membership_spy.reset_mock()

    request_created_conditional_access_spy = mocker.patch.object(
        request_hook, "role_request_created", return_value=[None]
    )

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert role_request.status == AccessRequestStatus.PENDING
    assert role_request.resolved_at is None
    assert role_request.resolver_user_id is None
    assert role_request.resolution_reason == ""
    assert request_created_notification_spy.call_count == 1
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 0

    _, kwargs = request_created_conditional_access_spy.call_args
    assert role_request == kwargs["role_request"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]


def test_auto_resolve_create_role_request_with_time_limit_constraint_tag(
    app: Flask,
    db: SQLAlchemy,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    tag: Tag,
    mocker: MockerFixture,
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    tag.constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
    }
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    notification_hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(notification_hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(notification_hook, "access_role_request_completed")
    request_hook = get_conditional_access_hook()
    request_created_conditional_access_spy = mocker.patch.object(
        request_hook,
        "role_request_created",
        return_value=[
            ConditionalAccessResponse(
                approved=True,
                reason="Auto-Approved",
                ending_at=datetime.now() + timedelta(seconds=SEVEN_DAYS_IN_SECONDS),
            ),
        ],
    )
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert role_request.status == AccessRequestStatus.APPROVED
    assert role_request.resolved_at is not None
    assert role_request.resolver_user_id is None
    assert role_request.resolution_reason == "Auto-Approved"
    assert request_created_notification_spy.call_count == 0
    assert request_completed_notification_spy.call_count == 0
    assert request_created_conditional_access_spy.call_count == 1
    assert add_membership_spy.call_count == 1

    _, kwargs = request_created_conditional_access_spy.call_args
    assert role_request == kwargs["role_request"]
    assert role_group == kwargs["role"]
    assert okta_group == kwargs["group"]
    assert user == kwargs["requester"]
    assert len(kwargs["group_tags"]) == 1
    assert tag in kwargs["group_tags"]


def test_time_limit_constraint_tag(
    app: Flask,
    db: SQLAlchemy,
    access_app: App,
    role_group: RoleGroup,
    app_group: AppGroup,
    user: OktaUser,
    tag: Tag,
    mocker: MockerFixture,
) -> None:
    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    # Add App
    db.session.add(access_app)

    # Add User
    db.session.add(user)
    db.session.commit()

    # Add app group that no one owns
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    db.session.commit()

    # Add role group that user owns
    db.session.add(role_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    # Add tag
    tag.constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
    }
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id))
    db.session.commit()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")

    # Make request for more than time limit
    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=app_group,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    # Ending time is None (more than time limit)
    assert kwargs["role_request"].request_ending_at is None
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user

    # Try to approve for more than time limit
    ApproveRoleRequest(
        role_request=role_request,
        approver_user=access_owner,
        ending_at=datetime.now() + timedelta(seconds=SEVEN_DAYS_IN_SECONDS),
    ).execute()

    # Should only be approved for time limit if approved time is higher
    approval = RoleGroupMap.query.filter(RoleGroupMap.role_group == role_group).first()
    # Make sure approval time is correct (could be a couple milliseconds off from calculated which is okay)
    approval_time = approval.ended_at.replace(tzinfo=timezone.utc)
    expected_approval_time = datetime.now(timezone.utc) + timedelta(seconds=THREE_DAYS_IN_SECONDS)
    assert expected_approval_time > approval_time
    assert expected_approval_time - approval_time < timedelta(minutes=1)


def test_owner_cant_add_self_constraint_tag(
    app: Flask,
    db: SQLAlchemy,
    access_app: App,
    role_group: RoleGroup,
    app_group: AppGroup,
    user: OktaUser,
    tag: Tag,
    mocker: MockerFixture,
) -> None:
    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    # Add App
    db.session.add(access_app)

    # Add Users
    user2 = OktaUserFactory.create()
    db.session.add(user)
    db.session.add(user2)
    db.session.commit()

    # Add app group that no one owns
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)

    db.session.commit()

    # Add role group that user owns
    db.session.add(role_group)
    db.session.commit()

    ModifyGroupUsers(
        group=role_group, members_to_add=[user.id, user2.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=app_group, owners_to_add=[user2.id], sync_to_okta=False).execute()

    # Add tag
    tag.constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
    }
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id))
    db.session.commit()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_role_request_completed")
    add_membership_spy = mocker.patch.object(okta, "async_add_user_to_group")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=app_group,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1
    _, kwargs = request_created_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user
    # since the only group owner is blocked, the request notification should be forwarded to Access admins
    assert len(kwargs["approvers"]) == 1
    assert kwargs["approvers"][0] == access_owner

    # user2 owns the group and is a member of the requester role, should fail
    should_fail = ApproveRoleRequest(
        role_request=role_request,
        approver_user=user2,
    ).execute()
    assert should_fail.status == AccessRequestStatus.PENDING

    should_pass = ApproveRoleRequest(
        role_request=role_request,
        approver_user=access_owner,
    ).execute()
    assert should_pass.status == AccessRequestStatus.APPROVED

    assert add_membership_spy.call_count == 2
    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == should_pass
    assert kwargs["role"] == role_group
    assert kwargs["group"] == app_group
    assert kwargs["requester"] == user


def test_role_request_approval_via_direct_add(
    client: FlaskClient,
    app: Flask,
    db: SQLAlchemy,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    mocker: MockerFixture,
) -> None:
    okta_group2 = OktaGroupFactory.create()

    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(okta_group2)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    request_created_notification_spy = mocker.patch.object(hook, "access_role_request_created")
    request_completed_notification_spy = mocker.patch.object(hook, "access_role_request_completed")

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    ModifyRoleGroups(
        role_group=role_group, groups_to_add=[okta_group.id], sync_to_okta=False, created_reason="test"
    ).execute()

    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == okta_group
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 1
    assert access_owner in kwargs["approvers"]

    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200
    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 0
    assert data["members"][0] == user.id

    request_created_notification_spy.reset_mock()
    request_completed_notification_spy.reset_mock()

    role_request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group2,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert role_request is not None
    assert request_created_notification_spy.call_count == 1

    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    ModifyRoleGroups(
        role_group=role_group, owner_groups_to_add=[okta_group2.id], sync_to_okta=False, created_reason="test"
    ).execute()

    assert request_completed_notification_spy.call_count == 1
    _, kwargs = request_completed_notification_spy.call_args
    assert kwargs["role_request"] == role_request
    assert kwargs["role"] == role_group
    assert kwargs["group"] == okta_group2
    assert kwargs["requester"] == user
    assert len(kwargs["approvers"]) == 2
    assert access_owner in kwargs["approvers"]

    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group2.id)
    rep = client.get(group_url)
    assert rep.status_code == 200
    data = rep.get_json()
    assert len(data["owners"]) == 1
    assert len(data["members"]) == 0
    assert data["owners"][0] == user.id
