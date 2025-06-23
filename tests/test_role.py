from datetime import UTC, datetime, timedelta
from typing import Any

from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.operations import CreateAccessRequest, ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import RoleGroupFactory


def test_get_role(
    client: FlaskClient,
    db: SQLAlchemy,
    role_group: RoleGroup,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    # test 404
    role_url = url_for("api-roles.role_by_id", role_id="randomid")
    rep = client.get(role_url)
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(role_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(group=okta_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=app_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[
            okta_group.id,
            app_group.id,
        ],
        owner_groups_to_add=[
            okta_group.id,
            app_group.id,
        ],
        sync_to_okta=False,
    ).execute()

    role_group_id = role_group.id
    role_group_name = role_group.name
    app_group_id = app_group.id
    okta_group_id = okta_group.id

    db.session.expunge_all()

    # test get role
    role_url = url_for("api-roles.role_by_id", role_id=role_group_id)
    rep = client.get(role_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == role_group_name

    app_url = url_for("api-roles.role_by_id", role_id=app_group_id)
    rep = client.get(app_url)
    assert rep.status_code == 404

    app_url = url_for("api-roles.role_by_id", role_id=okta_group_id)
    rep = client.get(app_url)
    assert rep.status_code == 404


def test_get_role_members(client: FlaskClient, db: SQLAlchemy, role_group: RoleGroup, okta_group: OktaGroup) -> None:
    # test 404
    role_url = url_for("api-roles.role_members_by_id", role_id="randomid")
    rep = client.get(role_url)
    assert rep.status_code == 404

    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.commit()

    # test get role group members
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.get(role_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 0
    assert len(data["groups_owned_by_role"]) == 0

    db.session.add(RoleGroupMap(group_id=okta_group.id, role_group_id=role_group.id))
    db.session.commit()

    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.get(role_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 0

    db.session.add(RoleGroupMap(group_id=okta_group.id, role_group_id=role_group.id, is_owner=True))
    db.session.commit()

    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.get(role_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id


def test_put_role_members(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    # test 404
    role_url = url_for("api-roles.role_members_by_id", role_id="randomid")
    rep = client.put(role_url)
    assert rep.status_code == 404

    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(user)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[], sync_to_okta=False).execute()

    membership_access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    ownership_access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()
    assert membership_access_request is not None
    assert ownership_access_request is not None

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    # test put role group members
    data: dict[str, Any] = {
        "groups_to_add": [],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 0
    assert len(data["groups_owned_by_role"]) == 0
    assert membership_access_request.status == AccessRequestStatus.PENDING
    assert membership_access_request.approved_membership_id is None
    assert ownership_access_request.status == AccessRequestStatus.PENDING
    assert ownership_access_request.approved_membership_id is None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 0
    # Mypy cannot infer that the status has changed and is no longer PENDING
    # https://github.com/python/mypy/issues/9005
    assert membership_access_request.status == AccessRequestStatus.APPROVED  # type: ignore [comparison-overlap]
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.PENDING
    assert ownership_access_request.approved_membership_id is None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id
    assert membership_access_request.status == AccessRequestStatus.APPROVED
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.APPROVED
    assert ownership_access_request.approved_membership_id is not None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [],
        "groups_to_remove": [okta_group.id],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 0
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id
    assert membership_access_request.status == AccessRequestStatus.APPROVED
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.APPROVED
    assert ownership_access_request.approved_membership_id is not None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [okta_group.id],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 1

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 0
    assert len(data["groups_owned_by_role"]) == 0
    assert membership_access_request.status == AccessRequestStatus.APPROVED
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.APPROVED
    assert ownership_access_request.approved_membership_id is not None


def test_get_all_role(client: FlaskClient, db: SQLAlchemy) -> None:
    groups_url = url_for("api-roles.roles")
    groups = RoleGroupFactory.create_batch(10)

    db.session.add_all(groups)
    db.session.commit()

    rep = client.get(groups_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for group in groups:
        assert any(u["id"] == group.id for u in results["results"])

    rep = client.get(groups_url, query_string={"q": "r"})
    assert rep.status_code == 200

    results = rep.get_json()
    for group in groups:
        assert any(u["id"] == group.id for u in results["results"])


def test_complex_role_modifications(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    access_app: App,
    user: OktaUser,
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(access_app)
    db.session.add(user)
    db.session.commit()

    user_id = user.id

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    # Add user to okta group and role group
    data: dict[str, Any] = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 2

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 0

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 3

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 0

    # Then add the role group as a member of the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 0

    # Remove the role group as a member of the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [],
        "groups_to_remove": [okta_group.id],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    # The user is still in the okta group, so they shouldn't be removed
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 3

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 0
    assert len(data["groups_owned_by_role"]) == 0

    # Add the role group back as a member of the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 0

    # Remove the user from the role_group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [user_id],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    # The user is still in the okta group, so they shouldn't be removed
    # But they are being removed from the role group
    assert remove_user_from_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 2

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 0

    # Add the user back to the role_group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 0

    # Add user as owner to okta group and role group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["members"][0] == user_id

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 6

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Then add the role group as an owner of the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 7

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Remove the okta group as a owner of the role group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [okta_group.id],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 6

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 0

    # Add the role group back as an owner of the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 7

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Remove the user from ownership of the role_group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [user_id],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 6

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 0

    # Add the user back as owner to the role_group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 7

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Remove the user from the role_group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [user_id],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    # The user is still in the okta group, so they shouldn't be removed
    # But they are being removed from the role group
    assert remove_user_from_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Add the user back to the role_group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 7

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Modify role group type from role group to app group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    update_group_spy = mocker.patch.object(okta, "update_group")

    app_group_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated"
    data = {
        "type": "app_group",
        "name": app_group_name,
        "description": "new description app_group",
        "app_id": access_app.id,
    }

    group_id = role_group.id
    okta_group_id = okta_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert update_group_spy.call_count == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert data["type"] == "app_group"
    assert data["name"] == app_group_name
    assert data["description"] == "new description app_group"
    assert data["id"] == group_id

    app_group = db.session.get(AppGroup, group_id)

    # Modify group type back to role group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    update_group_spy.reset_mock()

    role_group_name = f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Updated"

    data = {
        "type": "role_group",
        "name": role_group_name,
        "description": "new description role_group",
    }

    group_id = app_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert update_group_spy.call_count == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert data["type"] == "role_group"
    assert data["name"] == role_group_name
    assert data["description"] == "new description role_group"
    assert data["id"] == group_id

    role_group = db.session.get(RoleGroup, group_id)
    okta_group = db.session.get(OktaGroup, okta_group_id)

    # Add the role group back as a member and owner of the okta group, for a limited time
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "groups_added_ending_at": datetime.now(UTC) + timedelta(days=3),
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now()).count() == 2

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Add the user to the role_group for a limited time
    # less than the amount the okta group is a member of the role group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=1),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4
    # User should only be added for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > db.func.now(),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=2)),
        ).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4)),
        ).count()
        == 0
    )

    # Add the user to the role_group for a limited time
    # greater than the amount the okta group is a member of the role group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=7),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4
    # User should be added for the length of the okta group membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4)),
        ).count()
        == 2
    )
    # User should only be added for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8)),
        ).count()
        == 1
    )

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Add the role group back as a member and owner of the okta group, for an unlimited time
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4
    # User should only be in associated groups for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8)),
        ).count()
        == 3
    )

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Add the user back to the role_group for a limited time
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=5),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4
    # User should only be in associated groups for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=4)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=6)),
        ).count()
        == 3
    )

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Add the role group as a member and owner of the okta group, for a limited time
    # longer than the user membership to the role
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "groups_added_ending_at": datetime.now(UTC) + timedelta(days=14),
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4
    # User should only be in associated groups for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=4)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=6)),
        ).count()
        == 3
    )

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Add the role group as a member and owner of the okta group, for a limited time
    # less than the user membership to the role
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "groups_added_ending_at": datetime.now(UTC) + timedelta(days=3),
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 4
    # User should only be in associated groups for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=4)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=6)),
        ).count()
        == 1
    )
    # User should be added for the length of the okta group membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4)),
        ).count()
        == 2
    )

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Add the user back to the role_group for an unlimited time
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5
    # User should only be in associated groups for the length of their membership to the role group
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4)),
        ).count()
        == 2
    )

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Add the role group back as a member and owner of the okta group, for an unlimited time
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 7

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Remove the user from the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [user_id],
        "owners_to_remove": [user_id],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Modify okta group type from okta group to role group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    update_group_spy = mocker.patch.object(okta, "update_group")

    role_group_name = f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Updated-Again"
    data = {
        "type": "role_group",
        "name": role_group_name,
        "description": "new description role_group",
    }

    okta_group_id = okta_group.id
    role_group_id = role_group.id
    group_url = url_for("api-groups.group_by_id", group_id=okta_group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert update_group_spy.call_count == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert data["type"] == "role_group"
    assert data["name"] == role_group_name
    assert data["description"] == "new description role_group"
    assert data["id"] == okta_group_id

    # Modify group type back from role group to okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    update_group_spy = mocker.patch.object(okta, "update_group")

    okta_group_name = "Updated-Again"
    data = {
        "type": "okta_group",
        "name": okta_group_name,
        "description": "new description okta_group",
    }

    group_url = url_for("api-groups.group_by_id", group_id=okta_group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert update_group_spy.call_count == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert data["type"] == "okta_group"
    assert data["name"] == okta_group_name
    assert data["description"] == "new description okta_group"
    assert data["id"] == okta_group_id

    # Add the role group back as a member and owner of the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 7

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Remove the user from the okta group
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [user_id],
        "owners_to_remove": [user_id],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 5

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user_id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user_id

    # Test deleting the role_group
    # user should be removed from the group in Okta
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    delete_group_spy = mocker.patch.object(okta, "async_delete_group")

    group_url = url_for("api-groups.group_by_id", group_id=role_group_id)
    rep = client.delete(group_url)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 1
    assert delete_group_spy.call_count == 1
    assert OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count() == 1
    assert db.session.get(OktaGroup, group_id).deleted_at is not None


# Do not renew functionality test
# Since this field is only for expiring access, there are no checks for it anywhere in the API (only in the front end).
# Test is just to make sure the field is set correctly
def test_do_not_renew(
    db: SQLAlchemy,
    client: FlaskClient,
    mocker: MockerFixture,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[okta_group.id],
        sync_to_okta=False,
    ).execute()

    # need the RoleGroupMap id to pass in later
    role_group_map = RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role_group.id).first()

    # set one user to renew and one do not renew
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    data: dict[str, Any] = {
        "groups_to_add": [],
        "owner_groups_to_add": [],
        "groups_should_expire": [role_group_map.id],
        "owner_groups_should_expire": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert okta_group.id in data["groups_in_role"]
    assert len(data["groups_owned_by_role"]) == 0

    # get OktaUserGroupMembers, check expiration dates and should_expire
    membership_role = (
        RoleGroupMap.query.filter(
            db.or_(
                RoleGroupMap.ended_at.is_(None),
                RoleGroupMap.ended_at > db.func.now(),
            )
        )
        .filter(RoleGroupMap.role_group_id == role_group.id)
        .all()
    )

    assert len(membership_role) == 1
    assert membership_role[0].ended_at == expiration_datetime
    assert membership_role[0].should_expire is True
