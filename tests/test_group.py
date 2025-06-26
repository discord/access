from datetime import datetime, timedelta
from typing import Any, Dict, Protocol, cast
from unittest.mock import Mock

from factory import Faker
from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from okta.models.group import Group
from pytest_mock import MockerFixture

from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    Tag,
)
from api.operations import CreateAccessRequest, ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import AppGroupFactory, OktaGroupFactory, OktaUserFactory, RoleGroupFactory


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


def test_get_group(
    client: FlaskClient,
    db: SQLAlchemy,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    # test 404
    group_url = url_for("api-groups.group_by_id", group_id="randomid")
    rep = client.get(group_url)
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
        groups_to_add=[okta_group.id, app_group.id],
        owner_groups_to_add=[okta_group.id, app_group.id],
        sync_to_okta=False,
    ).execute()

    # test get group
    group_url = url_for("api-groups.group_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == okta_group.name

    group_url = url_for("api-groups.group_by_id", group_id=role_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == role_group.name

    group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == app_group.name


def test_get_group_members(client: FlaskClient, db: SQLAlchemy, okta_group: OktaGroup, user: OktaUser) -> None:
    # test 404
    group_url = url_for("api-groups.group_members_by_id", group_id="randomid")
    rep = client.get(group_url)
    assert rep.status_code == 404

    db.session.add(okta_group)
    db.session.add(user)
    db.session.commit()

    # test get group members
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 0

    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=okta_group.id))
    db.session.commit()

    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 0

    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=okta_group.id, is_owner=True))
    db.session.commit()

    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id


def test_put_group(
    client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, okta_group: OktaGroup, access_app: App, tag: Tag
) -> None:
    # test 404
    group_url = url_for("api-groups.group_by_id", group_id="randomid")
    rep = client.put(group_url)
    assert rep.status_code == 404

    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(tag)
    db.session.commit()
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)

    # test update group
    update_group_spy = mocker.patch.object(okta, "update_group")

    _update_group_type(client, okta_group, update_group_spy, "okta_group", {"tags_to_add": [tag.id]}, 1)
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "app_group",
        {
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated",
            "app_id": access_app.id,
            "tags_to_remove": [tag.id],
        },
        1,
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "role_group",
        {
            "name": f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Updated",
            "tags_to_add": [tag.id],
            "tags_to_remove": [tag.id],
        },
    )
    _update_group_type(client, okta_group, update_group_spy, "okta_group")
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "role_group",
        {
            "name": f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Updated",
            "tags_to_add": [tag.id],
            "tags_to_remove": [],
        },
        1,
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "app_group",
        {
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated",
            "app_id": access_app.id,
            "tags_to_add": [tag.id],
        },
        2,
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "okta_group",
        {
            "tags_to_add": [],
            "tags_to_remove": [tag.id],
        },
    )

    # Updating the name of the built-in app owners group should fail
    builtin_access_owners_group_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    builtin_access_owners_group = AppGroup.query.filter(
        AppGroup.name == builtin_access_owners_group_name, AppGroup.is_owner
    ).first()
    update_group_spy.reset_mock()

    data: dict[str, Any] = {
        "type": "okta_group",
        "name": "Updated",
        "description": "new description",
    }
    group_id = builtin_access_owners_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    # Updating tags is allowed, but nothing else
    update_group_spy.reset_mock()
    data.update(
        {
            "tags_to_add": [tag.id],
        }
    )
    group_id = builtin_access_owners_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    ret_data = rep.get_json()
    assert ret_data["type"] == "app_group"
    assert ret_data["name"] == builtin_access_owners_group_name
    assert ret_data["description"] != "new description"
    assert ret_data["id"] == group_id
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1

    update_group_spy.reset_mock()
    data.update(
        {
            "tags_to_remove": [tag.id],
        }
    )
    group_id = builtin_access_owners_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    ret_data = rep.get_json()
    assert ret_data["type"] == "app_group"
    assert ret_data["name"] == builtin_access_owners_group_name
    assert ret_data["description"] != "new description"
    assert ret_data["id"] == group_id
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0


def _update_group_type(
    client: FlaskClient,
    okta_group: OktaGroup,
    update_group_spy: Mock,
    group_type: str,
    extra_data: Dict[str, Any] = {},
    expected_tags_count: int = 0,
) -> None:
    update_group_spy.reset_mock()

    data = {
        "type": group_type,
        "name": "Updated",
        "description": f"new description {group_type}",
    }
    data.update(extra_data)

    group_id = okta_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    ret_data = rep.get_json()
    assert ret_data["type"] == group_type
    assert ret_data["name"] == data["name"]
    assert ret_data["description"] == f"new description {group_type}"
    assert ret_data["id"] == group_id
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 1
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == expected_tags_count


def test_put_group_members(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    access_app: App,
    app_group: AppGroup,
    user: OktaUser,
) -> None:
    # test 404
    group_url = url_for("api-groups.group_members_by_id", group_id="randomid")
    rep = client.put(group_url)
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[
            app_group.id,
        ],
        owner_groups_to_add=[
            app_group.id,
        ],
        sync_to_okta=False,
    ).execute()

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

    role_associated_membership_access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()
    role_associated_ownership_access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()
    assert role_associated_membership_access_request is not None
    assert role_associated_ownership_access_request is not None

    # test put group members
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    data: dict[str, Any] = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 0
    assert membership_access_request.status == AccessRequestStatus.PENDING
    assert membership_access_request.approved_membership_id is None
    assert ownership_access_request.status == AccessRequestStatus.PENDING
    assert ownership_access_request.approved_membership_id is None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user.id],
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

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 0

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
        "members_to_add": [],
        "owners_to_add": [user.id],
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

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id

    assert membership_access_request.status == AccessRequestStatus.APPROVED
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.APPROVED
    assert ownership_access_request.approved_membership_id is not None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [user.id],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id

    assert membership_access_request.status == AccessRequestStatus.APPROVED
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.APPROVED
    assert ownership_access_request.approved_membership_id is not None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [user.id],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 1

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 0

    assert membership_access_request.status == AccessRequestStatus.APPROVED
    assert membership_access_request.approved_membership_id is not None
    assert ownership_access_request.status == AccessRequestStatus.APPROVED
    assert ownership_access_request.approved_membership_id is not None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [user.id],
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

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id

    assert role_associated_membership_access_request.status == AccessRequestStatus.PENDING
    assert role_associated_membership_access_request.approved_membership_id is None
    assert role_associated_ownership_access_request.status == AccessRequestStatus.PENDING
    assert role_associated_ownership_access_request.approved_membership_id is None

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user.id],
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

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id

    assert role_associated_membership_access_request.status == AccessRequestStatus.APPROVED
    assert role_associated_membership_access_request.approved_membership_id is not None
    assert role_associated_ownership_access_request.status == AccessRequestStatus.APPROVED
    assert role_associated_ownership_access_request.approved_membership_id is not None


def test_delete_group(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    tag: Tag,
    app_group: AppGroup,
    access_app: App,
    user: OktaUser,
) -> None:
    # test 404
    group_url = url_for("api-groups.group_by_id", group_id="randomid")
    rep = client.delete(group_url)
    assert rep.status_code == 404

    db.session.add(okta_group)
    db.session.add(tag)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    # test delete group
    delete_group_spy = mocker.patch.object(okta, "async_delete_group")

    group_id = okta_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.delete(group_url)
    assert rep.status_code == 200
    assert delete_group_spy.call_count == 1
    assert db.session.get(OktaGroup, group_id).deleted_at is not None
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0

    db.session.add(user)
    db.session.add(access_app)
    db.session.commit()
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)
    db.session.commit()

    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    # test delete app group with access request
    delete_group_spy.reset_mock()

    group_id = app_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.delete(group_url)
    assert rep.status_code == 200
    assert delete_group_spy.call_count == 1
    assert db.session.get(OktaGroup, group_id).deleted_at is not None
    assert access_request is not None
    assert db.session.get(AccessRequest, access_request.id).status == AccessRequestStatus.REJECTED
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 1


def test_create_group(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    tag: Tag,
) -> None:
    # test bad data
    groups_url = url_for("api-groups.groups")
    data: dict[str, Any] = {}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 400

    db.session.add(tag)
    db.session.commit()

    # Cast faker to our Protocol type that has the pystr method
    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    data = {"type": "okta_group", "name": "Created", "description": "", "tags_to_add": [tag.id]}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1

    data = rep.get_json()
    assert db.session.get(OktaGroup, data["id"]) is not None
    assert data["name"] == "Created"
    assert data["description"] == ""
    assert data["active_group_tags"][0]["active_tag"]["id"] == tag.id
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1


def test_create_app_group(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    tag: Tag,
    access_app: App,
) -> None:
    # test bad data
    groups_url = url_for("api-groups.groups")
    data: dict[str, Any] = {}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 400

    db.session.add(access_app)
    db.session.add(tag)
    db.session.commit()
    db.session.add(AppTagMap(app_id=access_app.id, tag_id=tag.id))
    db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0

    app_group_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Created"
    data = {"type": "app_group", "app_id": access_app.id, "name": app_group_name, "description": ""}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1

    data = rep.get_json()
    assert db.session.get(OktaGroup, data["id"]) is not None
    assert data["name"] == app_group_name
    assert data["description"] == ""
    assert data["active_group_tags"][0]["active_tag"]["id"] == tag.id
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1


def test_get_all_group(client: FlaskClient, db: SQLAlchemy, access_app: App) -> None:
    groups_url = url_for("api-groups.groups")

    db.session.add(access_app)
    db.session.commit()

    groups = []
    groups.extend(OktaGroupFactory.create_batch(3))
    app_groups = AppGroupFactory.create_batch(3, app_id=access_app.id)
    groups.extend(app_groups)
    groups.extend(RoleGroupFactory.create_batch(3))
    db.session.add_all(groups)
    db.session.commit()

    rep = client.get(groups_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for group in groups:
        assert any(u["id"] == group.id for u in results["results"])

    rep = client.get(groups_url, query_string={"q": "App-"})
    assert rep.status_code == 200

    results = rep.get_json()
    assert len(results["results"]) == 4
    for group in app_groups:
        assert any(u["id"] == group.id for u in results["results"])


# Do not renew functionality test
# Since this field is only for expiring access, there are no checks for it anywhere in the API (only in the front end).
# Test is just to make sure the field is set correctly
def test_do_not_renew(
    db: SQLAlchemy, client: FlaskClient, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup
) -> None:
    user2 = OktaUserFactory.create()

    db.session.add(okta_group)
    db.session.add(user)
    db.session.add(user2)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    ModifyGroupUsers(
        group=okta_group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user.id, user2.id],
        sync_to_okta=False,
    ).execute()

    # need the OktaUserGroupMember id to pass in later
    membership_user2 = OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user2.id).first()

    # set one user to renew and one do not renew
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    data: dict[str, Any] = {
        "members_to_add": [user.id],
        "owners_to_add": [],
        "members_should_expire": [membership_user2.id],
        "owners_should_expire": [],
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

    data = rep.get_json()
    assert len(data["members"]) == 2
    assert user.id in data["members"] and user2.id in data["members"]
    assert len(data["owners"]) == 0

    # get OktaUserGroupMembers, check expiration dates and should_expire
    membership_user1 = (
        OktaUserGroupMember.query.filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        )
        .filter(OktaUserGroupMember.user_id == user.id)
        .all()
    )

    assert len(membership_user1) == 1
    assert membership_user1[0].ended_at is None
    assert membership_user1[0].should_expire is False

    membership_user2 = (
        OktaUserGroupMember.query.filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        )
        .filter(OktaUserGroupMember.user_id == user2.id)
        .all()
    )

    assert len(membership_user2) == 1
    assert membership_user2[0].ended_at == expiration_datetime
    assert membership_user2[0].should_expire is True
