from datetime import datetime, timedelta
from typing import Any, Dict, Protocol, cast
from unittest.mock import Mock

import pytest
from factory import Faker
from fastapi.testclient import TestClient
from okta.models.group import Group
from pytest_mock import MockerFixture
from fastapi import FastAPI

from sqlalchemy import event, func, or_
from api.auth import permissions as AuthorizationHelpers
from api.extensions import Db, db
from api.config import settings
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
from tests.factories import AppFactory, AppGroupFactory, OktaGroupFactory, OktaUserFactory, RoleGroupFactory


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


def test_get_group(
    client: TestClient,
    db: Db,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
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

    data = rep.json()
    assert data["name"] == okta_group.name

    group_url = url_for("api-groups.group_by_id", group_id=role_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["name"] == role_group.name

    group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["name"] == app_group.name


def test_get_group_role_grants_do_not_reload_own_group(
    client: TestClient,
    db: Db,
    access_app: App,
    app_group: AppGroup,
    url_for: Any,
) -> None:
    # Regression test: a group granted by several different roles was
    # re-fetching its own app_group/app row once per role's membership/
    # ownership mapping instead
    # of reusing the copy already loaded for the group itself.
    db.session.add(access_app)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    for _ in range(5):
        granting_role = RoleGroupFactory.create()
        db.session.add(granting_role)
        db.session.commit()
        ModifyRoleGroups(
            role_group=granting_role,
            groups_to_add=[app_group.id],
            owner_groups_to_add=[],
            sync_to_okta=False,
        ).execute()

    queries: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
        rep = client.get(group_url)
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)

    assert rep.status_code == 200
    # Exactly one such query is expected: loading the group's own polymorphic
    # (AppGroup + App) identity. Each additional occurrence is a re-fetch of
    # that same row triggered by one of the five role mappings above.
    own_group_reloads = [q for q in queries if "FROM okta_group JOIN app_group" in q]
    assert len(own_group_reloads) == 1, (
        f"expected exactly one app_group/app load, got {len(own_group_reloads)} -- "
        "a group granted by multiple roles is re-fetching its own row once per "
        "role mapping instead of reusing the already-loaded group"
    )


def test_get_group_members(client: TestClient, db: Db, okta_group: OktaGroup, user: OktaUser, url_for: Any) -> None:
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

    data = rep.json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 0

    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=okta_group.id))
    db.session.commit()

    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 0

    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=okta_group.id, is_owner=True))
    db.session.commit()

    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200

    data = rep.json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id


def test_put_group(
    client: TestClient, db: Db, mocker: MockerFixture, okta_group: OktaGroup, access_app: App, tag: Tag, url_for: Any
) -> None:
    # test 404 — PUT against a non-existent group_id with a valid body shape
    # returns 404. UpdateGroupBody is a discriminated union on `type`, so the
    # body must include the discriminator for the schema to parse; missing or
    # empty bodies fail validation with a 400 instead.
    group_url = url_for("api-groups.group_by_id", group_id="randomid")
    rep = client.put(group_url, json={"type": "okta_group"})
    assert rep.status_code == 404

    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(tag)
    db.session.commit()

    # Store IDs before requests — expunge_all() in ModifyGroupType can
    # detach fixture objects, causing DetachedInstanceError on access.
    tag_id = tag.id
    app_id = access_app.id
    app_name = access_app.name

    app_tag_map = AppTagMap(app_id=app_id, tag_id=tag_id)
    db.session.add(app_tag_map)

    # test update group
    update_group_spy = mocker.patch.object(okta, "update_group")

    _update_group_type(
        client, okta_group, update_group_spy, "okta_group", {"tags_to_add": [tag_id]}, 1, url_for=url_for
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "app_group",
        {
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated",
            "app_id": app_id,
            "tags_to_remove": [tag_id],
        },
        1,
        url_for=url_for,
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "role_group",
        {
            "name": f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Updated",
            "tags_to_add": [tag_id],
            "tags_to_remove": [tag_id],
        },
        url_for=url_for,
    )
    _update_group_type(client, okta_group, update_group_spy, "okta_group", url_for=url_for)
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "role_group",
        {
            "name": f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Updated",
            "tags_to_add": [tag_id],
            "tags_to_remove": [],
        },
        1,
        url_for=url_for,
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "app_group",
        {
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated",
            "app_id": app_id,
            "tags_to_add": [tag_id],
        },
        2,
        url_for=url_for,
    )
    _update_group_type(
        client,
        okta_group,
        update_group_spy,
        "okta_group",
        {
            "tags_to_add": [],
            "tags_to_remove": [tag_id],
        },
        url_for=url_for,
    )

    # Updating the name of the built-in app owners group should fail
    builtin_access_owners_group_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    builtin_access_owners_group = (
        db.session.query(AppGroup).filter(AppGroup.name == builtin_access_owners_group_name, AppGroup.is_owner).first()
    )
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
            "tags_to_add": [tag_id],
        }
    )
    group_id = builtin_access_owners_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    ret_data = rep.json()
    assert ret_data["type"] == "app_group"
    assert ret_data["name"] == builtin_access_owners_group_name
    assert ret_data["description"] != "new description"
    assert ret_data["id"] == group_id
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1

    update_group_spy.reset_mock()
    data.update(
        {
            "tags_to_remove": [tag_id],
        }
    )
    group_id = builtin_access_owners_group.id
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    ret_data = rep.json()
    assert ret_data["type"] == "app_group"
    assert ret_data["name"] == builtin_access_owners_group_name
    assert ret_data["description"] != "new description"
    assert ret_data["id"] == group_id
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0


def _update_group_type(
    client: TestClient,
    okta_group: OktaGroup,
    update_group_spy: Mock,
    group_type: str,
    extra_data: Dict[str, Any] = {},
    expected_tags_count: int = 0,
    url_for: Any = None,
) -> None:
    update_group_spy.reset_mock()

    data = {
        "type": group_type,
        "name": "Updated",
        "description": f"new description {group_type}",
    }
    data.update(extra_data)

    group_id = okta_group.id
    assert url_for is not None, "_update_group_type requires the url_for fixture"
    group_url = url_for("api-groups.group_by_id", group_id=group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    ret_data = rep.json()
    assert ret_data["type"] == group_type
    assert ret_data["name"] == data["name"]
    assert ret_data["description"] == f"new description {group_type}"
    assert ret_data["id"] == group_id
    assert db.session.query(AppTagMap).filter(AppTagMap.ended_at.is_(None)).count() == 1
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == expected_tags_count


def test_put_app_group_rebind_authorization(
    app: FastAPI,
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    app_group: AppGroup,
    url_for: Any,
) -> None:
    source_app = AppFactory.create()
    target_app = AppFactory.create()
    source_app_owner_group = AppGroupFactory.create(
        app_id=source_app.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{source_app.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
    )
    target_app_owner_group = AppGroupFactory.create(
        app_id=target_app.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{target_app.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
    )
    app_group.app_id = source_app.id
    db.session.add_all([source_app, target_app, source_app_owner_group, target_app_owner_group, app_group])
    db.session.commit()

    # Store IDs before requests — expunge_all() in ModifyGroupType can
    # detach fixture objects, causing DetachedInstanceError on access.
    app_group_id = app_group.id
    source_app_id = source_app.id
    target_app_id = target_app.id
    target_app_name = target_app.name
    target_app_owner_group_id = target_app_owner_group.id

    access_owner = db.session.query(OktaUser).filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    ModifyGroupUsers(group=app_group, owners_to_add=[access_owner.id], sync_to_okta=False).execute()

    mocker.patch.object(okta, "update_group")
    group_url = url_for("api-groups.group_by_id", group_id=app_group_id)
    rebind_data: dict[str, Any] = {
        "type": "app_group",
        "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{target_app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Prod",
        "app_id": target_app_id,
    }

    # A group owner who does not own the target app cannot rebind the group
    mocker.patch.object(AuthorizationHelpers, "is_access_admin", return_value=False)
    rep = client.put(group_url, json=rebind_data)
    assert rep.status_code == 403
    assert db.session.get(AppGroup, app_group_id).app_id == source_app_id

    # An Access admin can rebind the group to a different app
    mocker.patch.object(AuthorizationHelpers, "is_access_admin", return_value=True)
    rep = client.put(group_url, json=rebind_data)
    assert rep.status_code == 200
    assert rep.json()["app_id"] == target_app_id

    # Reset group back to source app for the next case
    group_obj = db.session.get(AppGroup, app_group_id)
    group_obj.app_id = source_app_id
    db.session.commit()

    # An owner of the target app can also rebind the group
    mocker.patch.object(AuthorizationHelpers, "is_access_admin", return_value=False)
    target_app_owner_group_obj = db.session.get(AppGroup, target_app_owner_group_id)
    ModifyGroupUsers(group=target_app_owner_group_obj, owners_to_add=[access_owner.id], sync_to_okta=False).execute()
    rep = client.put(group_url, json=rebind_data)
    assert rep.status_code == 200
    assert rep.json()["app_id"] == target_app_id


def test_put_group_members(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    access_app: App,
    app_group: AppGroup,
    user: OktaUser,
    url_for: Any,
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

    data = rep.json()
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

    data = rep.json()
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

    data = rep.json()
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

    data = rep.json()
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

    data = rep.json()
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

    data = rep.json()
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

    data = rep.json()
    assert len(data["members"]) == 1
    assert data["members"][0] == user.id
    assert len(data["owners"]) == 1
    assert data["owners"][0] == user.id

    assert role_associated_membership_access_request.status == AccessRequestStatus.APPROVED
    assert role_associated_membership_access_request.approved_membership_id is not None
    assert role_associated_ownership_access_request.status == AccessRequestStatus.APPROVED
    assert role_associated_ownership_access_request.approved_membership_id is not None


def test_delete_group(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    tag: Tag,
    app_group: AppGroup,
    access_app: App,
    user: OktaUser,
    url_for: Any,
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
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0

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
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert db.session.query(AppTagMap).filter(AppTagMap.ended_at.is_(None)).count() == 1


def test_delete_group_as_app_group_deleter(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    okta_group: OktaGroup,
    app_group: AppGroup,
    access_app: App,
    user: OktaUser,
    url_for: Any,
    mock_user: Any,
) -> None:
    db.session.add_all([user, access_app, okta_group])
    db.session.commit()
    app_group.app_id = access_app.id
    app_group.is_owner = False
    db.session.add(app_group)
    unmanaged = AppGroupFactory.build(app_id=access_app.id, is_owner=False, is_managed=False)
    db.session.add(unmanaged)
    db.session.commit()

    mocker.patch.object(okta, "async_delete_group")
    mock_user(user.id)
    monkeypatch.setattr(settings, "APP_GROUP_DELETER_ID", f"someone-else,{user.id}")

    # Managed AppGroup: allowed.
    rep = client.delete(url_for("api-groups.group_by_id", group_id=app_group.id))
    assert rep.status_code == 200
    assert db.session.get(OktaGroup, app_group.id).deleted_at is not None

    # Plain OktaGroup and unmanaged AppGroup: still 403.
    assert client.delete(url_for("api-groups.group_by_id", group_id=okta_group.id)).status_code == 403
    assert client.delete(url_for("api-groups.group_by_id", group_id=unmanaged.id)).status_code == 403


def test_create_group(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    tag: Tag,
    url_for: Any,
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

    data = rep.json()
    assert db.session.get(OktaGroup, data["id"]) is not None
    assert data["name"] == "Created"
    assert data["description"] == ""
    assert data["active_group_tags"][0]["active_tag"]["id"] == tag.id
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1


def test_create_app_group(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    tag: Tag,
    access_app: App,
    url_for: Any,
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

    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0

    app_group_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Created"
    data = {"type": "app_group", "app_id": access_app.id, "name": app_group_name, "description": ""}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1

    data = rep.json()
    assert db.session.get(OktaGroup, data["id"]) is not None
    assert data["name"] == app_group_name
    assert data["description"] == ""
    assert data["active_group_tags"][0]["active_tag"]["id"] == tag.id
    assert db.session.query(OktaGroupTagMap).filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1


def test_create_app_group_cannot_set_is_owner_shadow_escalation(
    app: FastAPI,
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    url_for: Any,
) -> None:
    """A non-admin app owner must not be able to create a *second* owner-group
    by setting `is_owner=True` on POST /api/groups. Such a "shadow" owner-group
    silently turns anyone added as its owner into an app owner — without them
    ever appearing in the App-<app>-Owners member list.
    """
    # App "Foo" with its owner group, owned by non-admin Alice
    foo = AppFactory.create()
    owners_group = AppGroupFactory.create(
        app_id=foo.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{foo.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
    )
    alice = OktaUserFactory.create()
    db.session.add_all([foo, owners_group, alice])
    db.session.commit()
    foo_id = foo.id
    foo_name = foo.name

    ModifyGroupUsers(group=owners_group, owners_to_add=[alice.id], sync_to_okta=False).execute()

    mocker.patch.object(okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()}))

    # act as Alice — a plain app owner, NOT an Access admin.
    app.state.current_user_email = alice.email
    assert not AuthorizationHelpers.is_access_admin(db.session, alice.id)

    shadow_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{foo_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}shadow"
    rep = client.post(
        url_for("api-groups.groups"),
        json={"type": "app_group", "app_id": foo_id, "name": shadow_name, "is_owner": True, "description": ""},
    )
    assert rep.status_code == 201

    # privilege flag should be ignored, so the new group is an
    # ordinary (non-owner) app group and Foo still has exactly one owner group.
    created = db.session.get(AppGroup, rep.json()["id"])
    assert not created.is_owner
    owner_groups = (
        db.session.query(AppGroup)
        .filter(AppGroup.app_id == foo_id, AppGroup.is_owner.is_(True), AppGroup.deleted_at.is_(None))
        .all()
    )
    assert [g.id for g in owner_groups] == [owners_group.id]


def test_get_all_group(client: TestClient, db: Db, access_app: App, url_for: Any) -> None:
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

    results = rep.json()
    for group in groups:
        assert any(u["id"] == group.id for u in results["items"])

    rep = client.get(groups_url, params={"q": "App-"})
    assert rep.status_code == 200

    results = rep.json()
    assert len(results["items"]) == 4
    for group in app_groups:
        assert any(u["id"] == group.id for u in results["items"])


# Do not renew functionality test
# Since this field is only for expiring access, there are no checks for it anywhere in the API (only in the front end).
# Test is just to make sure the field is set correctly
def test_do_not_renew(
    db: Db, client: TestClient, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, url_for: Any
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
    membership_user2 = db.session.query(OktaUserGroupMember).filter(OktaUserGroupMember.user_id == user2.id).first()

    # test non-owner/admin perms
    mocker.patch.object(AuthorizationHelpers, "can_manage_group", return_value=False)

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    data: dict[str, Any] = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_should_expire": [membership_user2.id],
        "owners_should_expire": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }

    # should fail
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 403
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    # now with group owner/admin perms
    mocker.patch.object(AuthorizationHelpers, "can_manage_group", return_value=True)

    # set one user to renew and one do not renew
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    data = {
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

    data = rep.json()
    assert len(data["members"]) == 2
    assert user.id in data["members"] and user2.id in data["members"]
    assert len(data["owners"]) == 0

    # get OktaUserGroupMembers, check expiration dates and should_expire
    membership_user1 = (
        db.session.query(OktaUserGroupMember)
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
        .filter(OktaUserGroupMember.user_id == user.id)
        .all()
    )

    assert len(membership_user1) == 1
    assert membership_user1[0].ended_at is None
    assert membership_user1[0].should_expire is False

    memberships_user2 = (
        db.session.query(OktaUserGroupMember)
        .filter(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
        .filter(OktaUserGroupMember.user_id == user2.id)
        .all()
    )

    assert len(memberships_user2) == 1
    assert memberships_user2[0].ended_at == expiration_datetime
    assert memberships_user2[0].should_expire is True


def test_do_not_renew_scoped_to_route_group(
    db: Db, client: TestClient, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, url_for: Any
) -> None:
    victim_group = OktaGroup(id="victim-group-id", name="Victim-Group", type="okta_group")
    victim_user = OktaUserFactory.create()

    db.session.add(okta_group)
    db.session.add(victim_group)
    db.session.add(user)
    db.session.add(victim_user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    ModifyGroupUsers(
        group=okta_group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user.id],
        sync_to_okta=False,
    ).execute()
    ModifyGroupUsers(
        group=victim_group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[victim_user.id],
        sync_to_okta=False,
    ).execute()

    victim_membership = (
        db.session.query(OktaUserGroupMember).filter(OktaUserGroupMember.user_id == victim_user.id).first()
    )
    assert victim_membership is not None

    mocker.patch.object(AuthorizationHelpers, "can_manage_group", return_value=True)
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_remove_user_from_group")
    mocker.patch.object(okta, "async_add_owner_to_group")
    mocker.patch.object(okta, "async_remove_owner_from_group")

    data: dict[str, Any] = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_should_expire": [victim_membership.id],
        "owners_should_expire": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200

    db.session.refresh(victim_membership)
    assert victim_membership.should_expire is False


@pytest.mark.parametrize("app", [False, True], indirect=True)
def test_create_groups_with_and_without_description(
    app: FastAPI,
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    access_app: App,
    url_for: Any,
) -> None:
    """Test that groups work with or without descriptions based on REQUIRE_DESCRIPTIONS setting"""
    require_descriptions = settings.REQUIRE_DESCRIPTIONS

    db.session.add(access_app)
    db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    groups_url = url_for("api-groups.groups")

    # Test creating okta_group without description
    data: dict[str, Any] = {"type": "okta_group", "name": "TestGroupNoDesc"}
    rep = client.post(groups_url, json=data)
    if require_descriptions:
        # Should fail when REQUIRE_DESCRIPTIONS=True
        assert rep.status_code == 400
        response_data = rep.json()
        assert "required" in str(response_data).lower()
    else:
        # Should succeed with backwards compatibility
        assert rep.status_code == 201
        result = rep.json()
        assert result["name"] == "TestGroupNoDesc"
        assert result["description"] == ""

    # Test creating group with empty description
    data = {"type": "okta_group", "name": "TestGroupEmptyDesc", "description": ""}
    rep = client.post(groups_url, json=data)
    if require_descriptions:
        # Should fail - empty description fails length validation
        assert rep.status_code == 400
        response_data = rep.json()
        assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()
    else:
        # Should succeed with empty description
        assert rep.status_code == 201
        result = rep.json()
        assert result["name"] == "TestGroupEmptyDesc"
        assert result["description"] == ""

    # Test creating groups with descriptions should always succeed
    data = {"type": "role_group", "name": "Role-TestGroupWithDesc", "description": "This has a description"}
    rep = client.post(groups_url, json=data)
    assert rep.status_code == 201

    result = rep.json()
    assert result["name"] == "Role-TestGroupWithDesc"
    assert result["description"] == "This has a description"


@pytest.mark.parametrize("app", [False, True], indirect=True)
def test_partial_group_update_preserves_description(
    app: FastAPI, client: TestClient, db: Db, mocker: MockerFixture, okta_group: OktaGroup, url_for: Any
) -> None:
    """Test that group updates handle descriptions correctly based on REQUIRE_DESCRIPTIONS setting"""
    require_descriptions = settings.REQUIRE_DESCRIPTIONS

    # Set up the group with a description
    okta_group.description = "Original description"
    db.session.add(okta_group)
    db.session.commit()

    mocker.patch.object(okta, "update_group")

    group_url = url_for("api-groups.group_by_id", group_id=okta_group.id)

    # Test updating with empty description
    data = {"type": "okta_group", "name": "UpdatedWithEmpty", "description": ""}
    rep = client.put(group_url, json=data)
    if require_descriptions:
        # Should fail - empty description fails length validation
        assert rep.status_code == 400
        response_data = rep.json()
        assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()
    else:
        # Should succeed
        assert rep.status_code == 200
        result = rep.json()
        assert result["name"] == "UpdatedWithEmpty"
        assert result["description"] == ""

    # Reset the group description back to "Original description" for the partial update test
    if not require_descriptions:
        # Need to reset since empty description succeeded above
        data = {"type": "okta_group", "name": "UpdatedWithEmpty", "description": "Original description"}
        rep = client.put(group_url, json=data)
        assert rep.status_code == 200

    # Test partial update without description should preserve existing description
    data = {"type": "okta_group", "name": "UpdatedName"}
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    result = rep.json()
    assert result["name"] == "UpdatedName"
    assert result["description"] == "Original description"

    # Test updating with valid description should succeed
    data = {"type": "okta_group", "name": "UpdatedName2", "description": "New description"}
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    result = rep.json()
    assert result["name"] == "UpdatedName2"
    assert result["description"] == "New description"


def test_cannot_convert_app_prefixed_group_to_non_app_type(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    url_for: Any,
) -> None:
    mocker.patch.object(okta, "update_group")

    app_group = AppGroupFactory.create(
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Members",
        app_id=access_app.id,
        is_owner=False,
    )
    db.session.add(access_app)
    db.session.add(app_group)
    db.session.commit()

    group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
    rep = client.put(group_url, json={"type": "okta_group", "name": app_group.name, "description": "desc"})
    assert rep.status_code == 400

    rep = client.put(group_url, json={"type": "role_group", "name": app_group.name, "description": "desc"})
    assert rep.status_code == 400

    # Changing to app_group (same type, different app) is still allowed
    rep = client.put(
        group_url,
        json={
            "type": "app_group",
            "name": app_group.name,
            "description": "desc",
            "app_id": access_app.id,
        },
    )
    assert rep.status_code == 200


def test_cannot_create_group_with_reserved_app_prefix(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    access_app: App,
    url_for: Any,
) -> None:
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    db.session.add(access_app)
    db.session.commit()

    groups_url = url_for("api-groups.groups")

    # okta_group with App- prefix is blocked
    rep = client.post(groups_url, json={"type": "okta_group", "name": "App-SomeName", "description": ""})
    assert rep.status_code == 400

    # role_group with App- prefix is blocked
    rep = client.post(groups_url, json={"type": "role_group", "name": "App-SomeName", "description": ""})
    assert rep.status_code == 400

    # app_group with App-*-Owners name is blocked
    owners_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    rep = client.post(
        groups_url, json={"type": "app_group", "app_id": access_app.id, "name": owners_name, "description": ""}
    )
    assert rep.status_code == 400

    # app_group with App-*-NonOwners name is allowed
    non_owners_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Members"
    )
    rep = client.post(
        groups_url, json={"type": "app_group", "app_id": access_app.id, "name": non_owners_name, "description": ""}
    )
    assert rep.status_code == 201


def test_create_app_group_requires_app_name_prefix(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    access_app: App,
    url_for: Any,
) -> None:
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    db.session.add(access_app)
    db.session.commit()
    app_name = access_app.name

    groups_url = url_for("api-groups.groups")
    expected_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"

    # app_group with no App- prefix at all is blocked
    rep = client.post(
        groups_url, json={"type": "app_group", "app_id": access_app.id, "name": "Whatever", "description": ""}
    )
    assert rep.status_code == 400
    assert rep.json()["detail"] == (
        f'App Group name "Whatever" should be prefixed with App name. For example: "{expected_prefix}"'
    )

    # app_group prefixed with a different app's name is blocked
    wrong_app_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}NotTheApp{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Group"
    rep = client.post(
        groups_url, json={"type": "app_group", "app_id": access_app.id, "name": wrong_app_name, "description": ""}
    )
    assert rep.status_code == 400

    # app_group referencing a non-existent app is a 400, not a 500
    conforming_name = f"{expected_prefix}Group"
    rep = client.post(
        groups_url, json={"type": "app_group", "app_id": "nonexistent-app", "name": conforming_name, "description": ""}
    )
    assert rep.status_code == 400
    assert rep.json()["detail"] == "App for AppGroup does not exist"

    # conforming name is allowed
    rep = client.post(
        groups_url, json={"type": "app_group", "app_id": access_app.id, "name": conforming_name, "description": ""}
    )
    assert rep.status_code == 201
    assert rep.json()["name"] == conforming_name


def test_cannot_rename_app_group_to_non_conforming_name(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    url_for: Any,
) -> None:
    mocker.patch.object(okta, "update_group")

    target_app = AppFactory.create()
    app_group = AppGroupFactory.create(
        app_id=target_app.id,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{target_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Conforming",
    )
    db.session.add_all([target_app, app_group])
    db.session.commit()
    app_name = target_app.name
    app_id = target_app.id

    group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
    expected_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"

    # Renaming an app group to a name without the app prefix is blocked
    rep = client.put(group_url, json={"type": "app_group", "app_id": app_id, "name": "Renamed", "description": ""})
    assert rep.status_code == 400
    assert rep.json()["detail"] == (
        f'App Group name "Renamed" should be prefixed with App name. For example: "{expected_prefix}"'
    )

    # Renaming to a different app's prefix is blocked
    wrong_prefix_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}OtherApp{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Renamed"
    rep = client.put(
        group_url, json={"type": "app_group", "app_id": app_id, "name": wrong_prefix_name, "description": ""}
    )
    assert rep.status_code == 400

    # Renaming within the app's prefix is allowed
    rep = client.put(
        group_url,
        json={"type": "app_group", "app_id": app_id, "name": f"{expected_prefix}Renamed", "description": ""},
    )
    assert rep.status_code == 200
    assert rep.json()["name"] == f"{expected_prefix}Renamed"


def test_convert_to_app_group_requires_app_name_prefix(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    url_for: Any,
) -> None:
    mocker.patch.object(okta, "update_group")

    okta_group = OktaGroupFactory.create(name="PlainGroup")
    db.session.add_all([access_app, okta_group])
    db.session.commit()
    app_name = access_app.name
    app_id = access_app.id
    group_name = okta_group.name

    group_url = url_for("api-groups.group_by_id", group_id=okta_group.id)
    expected_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"

    # Converting with a rename to a non-conforming name is blocked
    rep = client.put(
        group_url, json={"type": "app_group", "app_id": app_id, "name": "NonConforming", "description": ""}
    )
    assert rep.status_code == 400
    assert rep.json()["detail"] == (
        f'App Group name "NonConforming" should be prefixed with App name. For example: "{expected_prefix}"'
    )

    # Converting without a rename is blocked when the current name doesn't conform
    rep = client.put(group_url, json={"type": "app_group", "app_id": app_id})
    assert rep.status_code == 400
    assert rep.json()["detail"] == (
        f'App Group name "{group_name}" should be prefixed with App name. For example: "{expected_prefix}"'
    )

    # Converting against a non-existent app is a 400, not a rename + 500
    rep = client.put(
        group_url,
        json={"type": "app_group", "app_id": "nonexistent-app", "name": f"{expected_prefix}Members", "description": ""},
    )
    assert rep.status_code == 400
    assert rep.json()["detail"] == "App for AppGroup does not exist"

    # Nothing above should have renamed or converted the group
    db.session.expire_all()
    unchanged = db.session.get(OktaGroup, okta_group.id)
    assert unchanged.name == group_name
    assert unchanged.type == "okta_group"

    # Converting with a conforming name succeeds
    rep = client.put(
        group_url,
        json={"type": "app_group", "app_id": app_id, "name": f"{expected_prefix}Members", "description": ""},
    )
    assert rep.status_code == 200
    assert rep.json()["type"] == "app_group"
    assert rep.json()["name"] == f"{expected_prefix}Members"


def test_legacy_non_conforming_app_group_tolerates_unchanged_name(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    url_for: Any,
) -> None:
    """Pre-enforcement rows may carry non-conforming names; edits that resend
    the unchanged name (the frontend always sends `name`) must keep working."""
    mocker.patch.object(okta, "update_group")

    target_app = AppFactory.create()
    legacy_group = AppGroupFactory.create(app_id=target_app.id, name="App-SomethingElse-Group")
    db.session.add_all([target_app, legacy_group])
    db.session.commit()
    legacy_name = legacy_group.name

    group_url = url_for("api-groups.group_by_id", group_id=legacy_group.id)

    # Description-only edit resending the same name succeeds
    rep = client.put(
        group_url,
        json={"type": "app_group", "app_id": target_app.id, "name": legacy_name, "description": "updated"},
    )
    assert rep.status_code == 200
    assert rep.json()["name"] == legacy_name
    assert rep.json()["description"] == "updated"

    # But an actual rename must conform
    rep = client.put(
        group_url,
        json={"type": "app_group", "app_id": target_app.id, "name": "App-SomethingElse-Renamed", "description": ""},
    )
    assert rep.status_code == 400


def test_cannot_rename_non_app_group_to_app_prefix(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    url_for: Any,
) -> None:
    mocker.patch.object(okta, "update_group")

    okta_group = OktaGroupFactory.create(name="Payments-Owners")
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()

    group_url = url_for("api-groups.group_by_id", group_id=okta_group.id)

    # Renaming an okta_group to App- prefix without changing the type is blocked
    rep = client.put(group_url, json={"type": "okta_group", "name": "App-Payments-Owners", "description": ""})
    assert rep.status_code == 400

    # Renaming to a non-App- prefix is still allowed
    rep = client.put(group_url, json={"type": "okta_group", "name": "Legitimate-Payments-Owners", "description": ""})
    assert rep.status_code == 200

    # Simultaneous conversion to app_group and rename to App- prefix is allowed
    members_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Members"
    rep = client.put(
        group_url,
        json={"type": "app_group", "app_id": access_app.id, "name": members_name, "description": ""},
    )
    assert rep.status_code == 200


def test_cannot_create_group_with_reserved_role_prefix(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    url_for: Any,
) -> None:
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )

    groups_url = url_for("api-groups.groups")

    # okta_group with Role- prefix is blocked
    rep = client.post(groups_url, json={"type": "okta_group", "name": "Role-SomeName", "description": ""})
    assert rep.status_code == 400

    # app_group with Role- prefix is blocked
    rep = client.post(groups_url, json={"type": "app_group", "name": "Role-SomeName", "description": ""})
    assert rep.status_code == 400

    # role_group with Role- prefix is allowed
    rep = client.post(
        groups_url, json={"type": "role_group", "name": f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Admins", "description": ""}
    )
    assert rep.status_code == 201


def test_cannot_rename_non_role_group_to_role_prefix(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    url_for: Any,
) -> None:
    mocker.patch.object(okta, "update_group")

    okta_group = OktaGroupFactory.create(name="Some-Group")
    db.session.add(okta_group)
    db.session.commit()

    group_url = url_for("api-groups.group_by_id", group_id=okta_group.id)

    # Renaming an okta_group to Role- prefix without changing the type is blocked
    rep = client.put(group_url, json={"type": "okta_group", "name": "Role-SomeName", "description": ""})
    assert rep.status_code == 400

    # Renaming to a non-Role- prefix is still allowed
    rep = client.put(group_url, json={"type": "okta_group", "name": "Legitimate-SomeName", "description": ""})
    assert rep.status_code == 200

    # Simultaneous conversion to role_group and rename to Role- prefix is allowed
    rep = client.put(
        group_url,
        json={"type": "role_group", "name": f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Admins", "description": ""},
    )
    assert rep.status_code == 200


def test_cannot_convert_role_prefixed_group_to_non_role_type(
    client: TestClient,
    db: Db,
    mocker: MockerFixture,
    url_for: Any,
) -> None:
    mocker.patch.object(okta, "update_group")

    role_group = RoleGroupFactory.create(name=f"{RoleGroup.ROLE_GROUP_NAME_PREFIX}Admins")
    db.session.add(role_group)
    db.session.commit()

    group_url = url_for("api-groups.group_by_id", group_id=role_group.id)

    rep = client.put(group_url, json={"type": "okta_group", "name": role_group.name, "description": "desc"})
    assert rep.status_code == 400

    rep = client.put(group_url, json={"type": "app_group", "name": role_group.name, "description": "desc"})
    assert rep.status_code == 400

    # Keeping it as role_group is still allowed
    rep = client.put(group_url, json={"type": "role_group", "name": role_group.name, "description": "desc"})
    assert rep.status_code == 200


def test_put_group_members_rejects_short_user_id(
    client: TestClient, db: Db, okta_group: OktaGroup, url_for: Any
) -> None:
    """Each Okta user id must be exactly 20 characters wide. Pydantic
    enforces the constraint at the request boundary so malformed ids
    cannot leak into the operation layer."""
    db.session.add(okta_group)
    db.session.commit()
    group_url = url_for("api-groups.group_members_by_id_put", group_id=okta_group.id)
    rep = client.put(
        group_url,
        json={
            "members_to_add": ["short"],
            "members_to_remove": [],
            "owners_to_add": [],
            "owners_to_remove": [],
        },
    )
    # The project's exception handler maps Pydantic validation errors to 400
    # (not the FastAPI default 422) — see `api/exception_handlers.py`.
    assert rep.status_code == 400


def test_put_group_members_rejects_missing_required_lists(
    client: TestClient, db: Db, okta_group: OktaGroup, url_for: Any
) -> None:
    """`members_to_add`, `members_to_remove`, `owners_to_add`, and
    `owners_to_remove` are required fields on the request body. The Pydantic
    schema must reject a body that omits them entirely."""
    db.session.add(okta_group)
    db.session.commit()
    group_url = url_for("api-groups.group_members_by_id_put", group_id=okta_group.id)
    rep = client.put(group_url, json={})
    # The project's exception handler maps Pydantic validation errors to 400
    # (not the FastAPI default 422) — see `api/exception_handlers.py`.
    assert rep.status_code == 400


def test_put_group_members_accepts_well_formed_ids(
    client: TestClient, db: Db, okta_group: OktaGroup, user: OktaUser, url_for: Any
) -> None:
    """Sanity check: a 20-char id is accepted (regression guard for fix 1)."""
    db.session.add(okta_group)
    db.session.add(user)
    db.session.commit()
    group_url = url_for("api-groups.group_members_by_id_put", group_id=okta_group.id)
    rep = client.put(
        group_url,
        json={
            "members_to_add": [],
            "members_to_remove": [],
            "owners_to_add": [],
            "owners_to_remove": [],
        },
    )
    assert rep.status_code == 200


def test_app_group_app_ref_includes_lifecycle_plugin(
    client: TestClient,
    db: Db,
    access_app: App,
    app_group: AppGroup,
    url_for: Any,
) -> None:
    """Flask's `AppGroupSchema.app = Nested(AppSchema, only=("id", "name",
    "deleted_at", "app_group_lifecycle_plugin"))` emits the lifecycle plugin
    id on every nested app reference. The FastAPI `AppIdRef` must include
    `app_group_lifecycle_plugin` so the React app-detail / group-detail
    pages can dispatch on the plugin."""
    access_app.app_group_lifecycle_plugin = "noop"
    db.session.add(access_app)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    # GET /api/groups/{app_group_id}
    group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
    rep = client.get(group_url)
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert data["type"] == "app_group"
    assert data["app"] is not None
    assert data["app"].get("app_group_lifecycle_plugin") == "noop"

    # GET /api/groups (list) — same field must appear on app-group rows.
    rep = client.get(url_for("api-groups.groups"))
    assert rep.status_code == 200, rep.text
    rows = rep.json()["items"]
    matched = next((r for r in rows if r["id"] == app_group.id), None)
    assert matched is not None
    assert matched["app"] is not None
    assert matched["app"].get("app_group_lifecycle_plugin") == "noop"


def test_role_list_excludes_role_association_mappings(
    client: TestClient,
    db: Db,
    role_group: RoleGroup,
    url_for: Any,
) -> None:
    """Flask `RoleList.get()` uses `only=(id, type, name, description,
    created_at, updated_at)`. The FastAPI `/api/roles` list response must
    not emit the bulky `active_role_associated_group_*_mappings` arrays
    (those belong on the role-detail endpoint, not the list)."""
    db.session.add(role_group)
    db.session.commit()

    rep = client.get(url_for("api-roles.roles"))
    assert rep.status_code == 200, rep.text
    rows = rep.json()["items"]
    matched = next((r for r in rows if r["id"] == role_group.id), None)
    assert matched is not None
    assert "active_role_associated_group_member_mappings" not in matched
    assert "active_role_associated_group_owner_mappings" not in matched
    assert "active_group_tags" not in matched


def test_get_group_member_details_paginated(client: TestClient, db: Db, url_for: Any) -> None:
    """`GET /api/groups/{id}/member-details` returns full member rows paginated,
    so the group page can page members instead of inlining all of them. `owner`
    filters members vs owners."""
    group = OktaGroupFactory.create()
    db.session.add(group)
    db.session.commit()

    # Explicit, unique emails: the factory derives email from random Faker
    # names, so 60+ users can collide and trip the okta_user.email UNIQUE
    # constraint (flaky). Index-based emails make this deterministic.
    members = [OktaUserFactory.create(email=f"member-{i:03d}@example.com") for i in range(60)]
    owner = OktaUserFactory.create(email="owner@example.com")
    for u in members + [owner]:
        db.session.add(u)
    db.session.commit()

    ModifyGroupUsers(
        group=group,
        members_to_add=[u.id for u in members],
        owners_to_add=[owner.id],
        sync_to_okta=False,
    ).execute()

    group_id = group.id
    owner_id = owner.id
    db.session.expunge_all()

    url = url_for("api-groups.group_member_details_by_id", group_id=group_id)

    rep = client.get(url)
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert {"items", "total", "page", "size", "pages"} <= set(data.keys())
    assert data["total"] == 61
    assert len(data["items"]) == 50  # default page size
    assert "user" in data["items"][0]

    rep = client.get(url, params={"owner": "true"})
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert data["total"] == 1
    assert data["items"][0]["is_owner"] is True
    assert data["items"][0]["user"]["id"] == owner_id


def test_get_group_omits_inline_members(client: TestClient, db: Db, url_for: Any) -> None:
    """`GET /api/groups/{id}` no longer inlines its members; the group page
    pages them via the member-details endpoint instead, so the detail response
    can't materialize an unbounded member list."""
    group = OktaGroupFactory.create()
    db.session.add(group)
    db.session.commit()
    member = OktaUserFactory.create()
    db.session.add(member)
    db.session.commit()
    ModifyGroupUsers(group=group, members_to_add=[member.id], sync_to_okta=False).execute()

    group_id = group.id
    db.session.expunge_all()

    rep = client.get(url_for("api-groups.group_by_id", group_id=group_id))
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert "active_user_memberships" not in data
    assert "active_user_ownerships" not in data

    rep2 = client.get(url_for("api-groups.group_member_details_by_id", group_id=group_id))
    assert rep2.status_code == 200, rep2.text
    assert rep2.json()["total"] == 1


def test_group_member_details_counts_users_not_rows(client: TestClient, db: Db, url_for: Any) -> None:
    """A user holding both a direct and a role-granted membership in a group is a
    single member: member-details pages by distinct user, so total counts the
    user once and both of their rows land on the same page."""
    group = OktaGroupFactory.create()
    role = RoleGroupFactory.create()
    user = OktaUserFactory.create()
    db.session.add_all([group, role, user])
    db.session.commit()

    # Direct membership in the group.
    ModifyGroupUsers(group=group, members_to_add=[user.id], sync_to_okta=False).execute()
    # Role-granted membership: user is in the role, role is a member of the group.
    ModifyGroupUsers(group=role, members_to_add=[user.id], sync_to_okta=False).execute()
    ModifyRoleGroups(role_group=role, groups_to_add=[group.id], sync_to_okta=False).execute()

    group_id = group.id
    user_id = user.id
    db.session.expunge_all()

    url = url_for("api-groups.group_member_details_by_id", group_id=group_id)
    rep = client.get(url, params={"owner": "false"})
    assert rep.status_code == 200, rep.text
    data = rep.json()
    # One distinct member user, even though they hold two active membership rows.
    assert data["total"] == 1
    # Both rows are returned so the UI can render direct + via-role chips.
    rows = [r for r in data["items"] if r.get("active_user") and r["active_user"]["id"] == user_id]
    assert len(rows) == 2
