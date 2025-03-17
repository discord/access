from typing import Any, Protocol, cast

from factory import Faker
from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from okta.models.group import Group
from pytest_mock import MockerFixture

from api.models import (
    App,
    AppGroup,
    AppTagMap,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    Tag,
)
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory


def test_get_app(
    client: FlaskClient, db: SQLAlchemy, access_app: App, app_group: AppGroup, role_group: RoleGroup, user: OktaUser
) -> None:
    # test 404
    app_url = url_for("api-apps.app_by_id", app_id="randomid")
    rep = client.get(app_url)
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(user)
    db.session.add(role_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=app_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role_group, groups_to_add=[app_group.id], owner_groups_to_add=[app_group.id], sync_to_okta=False
    ).execute()

    app_id = access_app.id
    app_name = access_app.name
    app_description = access_app.description
    app_group_id = app_group.id
    role_group_id = role_group.id

    db.session.expunge_all()

    # test get app
    app_url = url_for("api-apps.app_by_id", app_id=app_id)
    rep = client.get(app_url)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == app_name
    assert data["description"] == app_description

    app_url = url_for("api-apps.app_by_id", app_id=app_group_id)
    rep = client.get(app_url)
    assert rep.status_code == 404

    app_url = url_for("api-apps.app_by_id", app_id=role_group_id)
    rep = client.get(app_url)
    assert rep.status_code == 404


def test_put_app(
    client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, tag: Tag
) -> None:
    # test 404
    app_url = url_for("api-apps.app_by_id", app_id="randomid")
    rep = client.put(app_url)
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(tag)
    db.session.commit()
    app_group.app_id = access_app.id
    app_group.name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}group"
    db.session.add(app_group)
    db.session.commit()

    old_app_group_id = app_group.id

    # test update app
    update_group_spy = mocker.patch.object(okta, "update_group")

    data = {"name": "Updated", "description": "new description", "tags_to_add": [tag.id]}

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    data = rep.get_json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["id"] == access_app.id
    assert (
        db.session.get(AppGroup, old_app_group_id).name
        == f"{AppGroup.APP_GROUP_NAME_PREFIX}Updated{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}group"
    )
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 1

    update_group_spy.reset_mock()

    data = {"name": "Updated", "tags_to_add": [tag.id], "tags_to_remove": []}
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.get_json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["id"] == access_app.id
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 1

    update_group_spy.reset_mock()

    data = {"name": "Updated", "tags_to_remove": [tag.id]}
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.get_json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["id"] == access_app.id
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0

    # Updating the name of the built-in Access app should fail
    builtin_access_app = App.query.filter(App.name == App.ACCESS_APP_RESERVED_NAME).first()
    builtin_access_owners_group = AppGroup.query.filter(
        AppGroup.app_id == builtin_access_app.id, AppGroup.is_owner.is_(True)
    ).first()
    app_url = url_for("api-apps.app_by_id", app_id=builtin_access_app.id)
    data = {"name": "UpdatedAccess"}
    rep = client.put(app_url, json=data)
    assert rep.status_code == 400

    # Updating tags is allowed, but nothing else
    data = {"name": "UpdatedAccess", "description": "new description", "tags_to_add": [tag.id]}
    update_group_spy.reset_mock()
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.get_json()
    assert data["name"] == App.ACCESS_APP_RESERVED_NAME
    assert data["description"] != "new description"
    assert data["id"] == builtin_access_app.id
    assert (
        db.session.get(AppGroup, builtin_access_owners_group.id).name
        == f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 1

    data = {"name": "UpdatedAccess", "description": "new description", "tags_to_remove": [tag.id]}
    update_group_spy.reset_mock()
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.get_json()
    assert data["name"] == App.ACCESS_APP_RESERVED_NAME
    assert data["description"] != "new description"
    assert data["id"] == builtin_access_app.id
    assert (
        db.session.get(AppGroup, builtin_access_owners_group.id).name
        == f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0


def test_delete_app(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, tag: Tag) -> None:
    # test 404
    app_url = url_for("api-apps.app_by_id", app_id="100000")
    rep = client.delete(app_url)
    assert rep.status_code == 404

    app_groups = AppGroupFactory.create_batch(3, app=access_app)
    db.session.add(access_app)
    db.session.add_all(app_groups)
    db.session.add(tag)
    db.session.commit()

    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    for app_group in app_groups:
        db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    app_id = access_app.id
    app_group_id = app_groups[0].id

    # test delete app
    delete_group_spy = mocker.patch.object(okta, "async_delete_group")

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)
    rep = client.delete(app_url)
    assert rep.status_code == 200
    assert delete_group_spy.call_count == 3
    assert db.session.get(App, app_id).deleted_at is not None
    assert db.session.get(AppGroup, app_group_id).deleted_at is not None
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


def test_create_app(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    tag: Tag,
) -> None:
    # test bad data
    apps_url = url_for("api-apps.apps")
    data: dict[str, Any] = {}
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 400

    db.session.add(tag)
    db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    data = {"name": "Created", "tags_to_add": [tag.id]}

    rep = client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.get_json()
    assert db.session.get(App, data["id"]) is not None

    assert data["name"] == "Created"
    assert data["description"] == ""
    assert data["active_app_tags"][0]["active_tag"]["id"] == tag.id

    app_groups = AppGroup.query.filter(AppGroup.app_id == data["id"]).all()
    assert len(app_groups) == 1
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 1
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 1

    test_app_group = AppGroup.query.filter(AppGroup.name == "App-Created-Owners", AppGroup.app_id == data["id"]).first()
    assert test_app_group is not None
    assert test_app_group.is_owner is True


def test_create_app_with_initial_owners(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    role_group: RoleGroup,
) -> None:
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    data = {"name": "Created", "initial_owner_id": user.id, "initial_owner_role_ids": [role_group.id]}

    apps_url = url_for("api-apps.apps")
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.get_json()
    assert db.session.get(App, data["id"]) is not None

    assert data["name"] == "Created"
    assert data["description"] == ""

    app_groups = AppGroup.query.filter(AppGroup.app_id == data["id"]).all()
    assert len(app_groups) == 1

    test_app_group = AppGroup.query.filter(AppGroup.name == "App-Created-Owners", AppGroup.app_id == data["id"]).first()
    assert test_app_group is not None
    assert test_app_group.is_owner is True

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == test_app_group.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.group_id == test_app_group.id)
        .filter(RoleGroupMap.role_group_id == role_group.id)
        .filter(RoleGroupMap.ended_at.is_(None))
        .count()
        == 2
    )


def test_create_app_with_additional_groups(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
) -> None:
    # test bad data
    apps_url = url_for("api-apps.apps")
    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {"name": "Test", "description": "test"},
        ],
    }
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 400

    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {"name": "App-WrongApp-Test", "description": "test"},
        ],
    }
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 400

    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {},
        ],
    }
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 400

    create_group_spy = mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {"name": "App-Created-Test", "description": "test"},
            {"name": "App-Created-Test2"},
        ],
    }

    apps_url = url_for("api-apps.apps")
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 3
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.get_json()
    assert db.session.get(App, data["id"]) is not None

    assert data["name"] == "Created"
    assert data["description"] == ""

    app_groups = AppGroup.query.filter(AppGroup.app_id == data["id"]).all()
    assert len(app_groups) == 3

    test_app_group = AppGroup.query.filter(AppGroup.name == "App-Created-Owners", AppGroup.app_id == data["id"]).first()
    assert test_app_group is not None
    assert test_app_group.is_owner is True

    test_app_group = AppGroup.query.filter(AppGroup.name == "App-Created-Test", AppGroup.app_id == data["id"]).first()
    assert test_app_group is not None
    assert test_app_group.description == "test"
    assert test_app_group.is_owner is False


def test_create_app_with_name_collision(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    app_group: AppGroup,
) -> None:
    app = AppFactory.create()
    app.name = "Test-Staging"
    db.session.add(app)
    db.session.commit()

    app_group.app_id = app.id
    app_group.name = "App-Test-Staging-Group"
    db.session.add(app_group)
    db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")

    data = {"name": "Test"}

    apps_url = url_for("api-apps.apps")
    rep = client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.get_json()
    assert db.session.get(App, data["id"]) is not None
    assert data["name"] == "Test"

    # Make sure new app doesn't end up with additional app groups from name collision
    app_groups = AppGroup.query.filter(AppGroup.app_id == data["id"]).all()
    assert len(app_groups) == 1

    # Make sure original app still has its app group
    app_groups = AppGroup.query.filter(AppGroup.app_id == app.id).all()
    assert len(app_groups) == 1


def test_get_all_app(client: FlaskClient, db: SQLAlchemy) -> None:
    apps_url = url_for("api-apps.apps")
    apps = AppFactory.create_batch(10)

    # Prefix all the app names with A so we can query for them easily
    for app in apps:
        app.name = f"A{app.name}"

    db.session.add_all(apps)
    db.session.commit()

    rep = client.get(apps_url)
    assert rep.status_code == 200

    results = rep.get_json()
    for app in apps:
        assert any(u["id"] == app.id for u in results["results"])

    rep = client.get(apps_url, query_string={"q": "a"})
    assert rep.status_code == 200

    results = rep.get_json()
    for app in apps:
        assert any(u["id"] == app.id for u in results["results"])
