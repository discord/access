from typing import Any, Protocol, cast

import pytest
from factory import Faker
from httpx import AsyncClient
from sqlalchemy import select
from okta.models.group import Group
from pytest_mock import MockerFixture
from fastapi import FastAPI

from api.config import settings
from api.extensions import Db
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
from tests.factories import AppFactory, AppGroupFactory, OktaGroupFactory, OktaUserFactory
from tests.helpers import db_count


async def test_get_app_omits_inline_groups(client: AsyncClient, db: Db, url_for: Any) -> None:
    """App detail no longer inlines its groups (and their memberships): those
    fields are gone so the response can't materialize every group's members.
    The React /apps page reads groups from the paginated /groups endpoint."""
    access_app_url = url_for("api-apps.app_by_id", app_id=App.ACCESS_APP_RESERVED_NAME)
    rep = await client.get(access_app_url)
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert data["name"] == App.ACCESS_APP_RESERVED_NAME
    assert "active_owner_app_groups" not in data
    assert "active_non_owner_app_groups" not in data


async def test_app_groups_endpoint_returns_owner_group(client: AsyncClient, db: Db, url_for: Any) -> None:
    """The conftest seeds the built-in Access app + App-Access-Owners group;
    it must surface under the paginated /groups endpoint as an owner group."""
    expected_owner_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    groups_url = url_for("api-apps.app_groups_by_id", app_id=App.ACCESS_APP_RESERVED_NAME)
    rep = await client.get(groups_url)
    assert rep.status_code == 200, rep.text
    items = rep.json()["items"]
    owner_groups = [g for g in items if g["is_owner"]]
    assert expected_owner_name in [g["name"] for g in owner_groups]
    for g in owner_groups:
        assert g["type"] == "app_group"
        assert g["is_owner"] is True


async def test_get_app(
    client: AsyncClient,
    db: Db,
    access_app: App,
    app_group: AppGroup,
    role_group: RoleGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    # test 404
    app_url = url_for("api-apps.app_by_id", app_id="randomid")
    rep = await client.get(app_url)
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(user)
    db.session.add(role_group)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=app_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
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
    rep = await client.get(app_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["name"] == app_name
    assert data["description"] == app_description

    app_url = url_for("api-apps.app_by_id", app_id=app_group_id)
    rep = await client.get(app_url)
    assert rep.status_code == 404

    app_url = url_for("api-apps.app_by_id", app_id=role_group_id)
    rep = await client.get(app_url)
    assert rep.status_code == 404


async def test_get_app_groups_paginated(client: AsyncClient, db: Db, user: OktaUser, url_for: Any) -> None:
    """`GET /api/apps/{app_id}/groups` returns the app's groups as a page bounded
    to 10 per page. Members are NOT inlined — each item carries member_count /
    owner_count, and the UI fetches members per-group from the paginated
    member-details endpoint — so a single huge group can't bloat the response."""
    app = AppFactory.create()
    db.session.add(app)
    db.session.add(user)
    await db.session.commit()

    groups = []
    for _ in range(25):
        ag = AppGroupFactory.create(app_id=app.id)
        db.session.add(ag)
        groups.append(ag)
    await db.session.commit()

    await ModifyGroupUsers(group=groups[0], members_to_add=[user.id], sync_to_okta=False).execute()

    app_id = app.id
    member_group_id = groups[0].id
    db.session.expunge_all()

    url = url_for("api-apps.app_groups_by_id", app_id=app_id)
    rep = await client.get(url)
    assert rep.status_code == 200, rep.text
    data = rep.json()

    # fastapi-pagination wire shape
    assert {"items", "total", "page", "size", "pages"} <= set(data.keys())
    # bounded to 10 per page even though the app has 25 groups
    assert data["size"] == 10
    assert len(data["items"]) == 10
    assert data["total"] == 25
    assert data["pages"] == 3

    # counts replace inline member arrays
    assert "active_user_memberships" not in data["items"][0]
    assert "active_user_ownerships" not in data["items"][0]
    assert "member_count" in data["items"][0]
    assert "owner_count" in data["items"][0]

    by_id = {g["id"]: g for g in data["items"]}
    if member_group_id in by_id:
        assert by_id[member_group_id]["member_count"] == 1
        assert by_id[member_group_id]["owner_count"] == 0


async def test_get_app_groups_search_by_user(client: AsyncClient, db: Db, url_for: Any) -> None:
    """`?q=` returns only the app's groups containing a member matching the
    query by name/email, computed server-side (the app page's user search)."""
    app = AppFactory.create()
    db.session.add(app)
    await db.session.commit()
    group_a = AppGroupFactory.create(app_id=app.id)
    group_b = AppGroupFactory.create(app_id=app.id)
    db.session.add_all([group_a, group_b])
    alice = OktaUserFactory.create(first_name="Alice", last_name="Xeno", email="alice.xeno@example.com")
    bob = OktaUserFactory.create(first_name="Bob", last_name="Yon", email="bob.yon@example.com")
    db.session.add_all([alice, bob])
    await db.session.commit()
    await ModifyGroupUsers(group=group_a, members_to_add=[alice.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=group_b, members_to_add=[bob.id], sync_to_okta=False).execute()

    app_id = app.id
    group_a_id = group_a.id
    db.session.expunge_all()

    url = url_for("api-apps.app_groups_by_id", app_id=app_id)
    rep = await client.get(url, params={"q": "alice"})
    assert rep.status_code == 200, rep.text
    ids = [g["id"] for g in rep.json()["items"]]
    assert ids == [group_a_id]

    rep = await client.get(url, params={"q": "bob.yon@example.com"})
    assert [g["id"] for g in rep.json()["items"]] == [group_b.id]


async def test_get_app_groups_size_capped_at_10(client: AsyncClient, db: Db, url_for: Any) -> None:
    """Requesting more than 10 per page is rejected so the bound can't be opted out of."""
    app = AppFactory.create()
    db.session.add(app)
    await db.session.commit()
    app_id = app.id
    db.session.expunge_all()

    url = url_for("api-apps.app_groups_by_id", app_id=app_id)
    rep = await client.get(url, params={"size": 20})
    assert rep.status_code == 400, rep.text
    assert "size" in rep.text


async def test_put_app(
    client: AsyncClient, db: Db, mocker: MockerFixture, access_app: App, app_group: AppGroup, tag: Tag, url_for: Any
) -> None:
    # test 404
    app_url = url_for("api-apps.app_by_id", app_id="randomid")
    rep = await client.put(app_url)
    assert rep.status_code == 404

    db.session.add(access_app)
    db.session.add(tag)
    await db.session.commit()
    app_group.app_id = access_app.id
    app_group.name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}group"
    db.session.add(app_group)
    await db.session.commit()

    old_app_group_id = app_group.id

    # test update app
    update_group_spy = mocker.patch.object(okta, "update_group")

    data = {"name": "Updated", "description": "new description", "tags_to_add": [tag.id]}

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    data = rep.json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["id"] == access_app.id
    assert (
        await db.session.get(AppGroup, old_app_group_id)
    ).name == f"{AppGroup.APP_GROUP_NAME_PREFIX}Updated{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}group"
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 1
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 1

    update_group_spy.reset_mock()

    data = {"name": "Updated", "tags_to_add": [tag.id], "tags_to_remove": []}
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["id"] == access_app.id
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 1
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 1

    update_group_spy.reset_mock()

    data = {"name": "Updated", "tags_to_remove": [tag.id]}
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["id"] == access_app.id
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 0
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 0

    # Updating the name of the built-in Access app should fail
    builtin_access_app = (await db.session.scalars(select(App).where(App.name == App.ACCESS_APP_RESERVED_NAME))).first()
    builtin_access_owners_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.app_id == builtin_access_app.id, AppGroup.is_owner.is_(True))
        )
    ).first()
    app_url = url_for("api-apps.app_by_id", app_id=builtin_access_app.id)
    data = {"name": "UpdatedAccess"}
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 400

    # The failed request's rollback on the shared session expired the
    # identity map; reload before reading ORM attributes.
    await db.session.refresh(tag)
    await db.session.refresh(builtin_access_app)
    await db.session.refresh(builtin_access_owners_group)

    # Updating tags is allowed, but nothing else
    data = {"name": "UpdatedAccess", "description": "new description", "tags_to_add": [tag.id]}
    update_group_spy.reset_mock()
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.json()
    assert data["name"] == App.ACCESS_APP_RESERVED_NAME
    assert data["description"] != "new description"
    assert data["id"] == builtin_access_app.id
    assert (
        (await db.session.get(AppGroup, builtin_access_owners_group.id)).name
        == f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 1
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 1

    data = {"name": "UpdatedAccess", "description": "new description", "tags_to_remove": [tag.id]}
    update_group_spy.reset_mock()
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    data = rep.json()
    assert data["name"] == App.ACCESS_APP_RESERVED_NAME
    assert data["description"] != "new description"
    assert data["id"] == builtin_access_app.id
    assert (
        (await db.session.get(AppGroup, builtin_access_owners_group.id)).name
        == f"{AppGroup.APP_GROUP_NAME_PREFIX}{App.ACCESS_APP_RESERVED_NAME}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 0
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 0


async def test_delete_app(
    client: AsyncClient, db: Db, mocker: MockerFixture, access_app: App, tag: Tag, url_for: Any
) -> None:
    # test 404
    app_url = url_for("api-apps.app_by_id", app_id="100000")
    rep = await client.delete(app_url)
    assert rep.status_code == 404

    app_groups = AppGroupFactory.create_batch(3, app=access_app)
    db.session.add(access_app)
    db.session.add_all(app_groups)
    db.session.add(tag)
    await db.session.commit()

    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    await db.session.commit()
    for app_group in app_groups:
        db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    await db.session.commit()

    app_id = access_app.id
    app_group_id = app_groups[0].id

    # test delete app
    delete_group_spy = mocker.patch.object(okta, "delete_group")

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)
    rep = await client.delete(app_url)
    assert rep.status_code == 200
    assert delete_group_spy.call_count == 3
    # The delete request's bulk update expired deleted_at on the identity-map
    # instances; expire fully so the get() calls below reload them.
    db.session.expire_all()
    assert (await db.session.get(App, app_id)).deleted_at is not None
    assert (await db.session.get(AppGroup, app_group_id)).deleted_at is not None
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 0
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 0


async def test_get_app_excludes_soft_deleted(
    client: AsyncClient, db: Db, mocker: MockerFixture, access_app: App, url_for: Any
) -> None:
    db.session.add(access_app)
    await db.session.commit()
    app_id = access_app.id
    app_name = access_app.name

    mocker.patch.object(okta, "delete_group")
    rep = await client.delete(url_for("api-apps.app_by_id", app_id=app_id))
    assert rep.status_code == 200
    # The delete request's bulk update expired deleted_at on the identity-map
    # instance; expire fully so the get() below reloads it.
    db.session.expire_all()
    assert (await db.session.get(App, app_id)).deleted_at is not None

    rep = await client.get(url_for("api-apps.app_by_id", app_id=app_id))
    assert rep.status_code == 404

    rep = await client.get(url_for("api-apps.app_by_id", app_id=app_name))
    assert rep.status_code == 404


# Define a Protocol that includes the pystr method
class FakerWithPyStr(Protocol):
    def pystr(self) -> str: ...


async def test_create_app(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    tag: Tag,
    url_for: Any,
) -> None:
    # test bad data
    apps_url = url_for("api-apps.apps")
    data: dict[str, Any] = {}
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 400

    db.session.add(tag)
    await db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    data = {"name": "Created", "tags_to_add": [tag.id]}

    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.json()
    assert await db.session.get(App, data["id"]) is not None

    assert data["name"] == "Created"
    assert data["description"] == ""
    assert data["active_app_tags"][0]["active_tag"]["id"] == tag.id

    app_groups = (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == data["id"]))).all()
    assert len(app_groups) == 1
    assert await db_count(db.session, select(OktaGroupTagMap).where(OktaGroupTagMap.ended_at.is_(None))) == 1
    assert await db_count(db.session, select(AppTagMap).where(AppTagMap.ended_at.is_(None))) == 1

    test_app_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.name == "App-Created-Owners", AppGroup.app_id == data["id"])
        )
    ).first()
    assert test_app_group is not None
    assert test_app_group.is_owner is True


async def test_create_app_with_initial_owners(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    role_group: RoleGroup,
    url_for: Any,
) -> None:
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    data = {"name": "Created", "initial_owner_id": user.id, "initial_owner_role_ids": [role_group.id]}

    apps_url = url_for("api-apps.apps")
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    # The create-app request staled the identity-map entries on the shared
    # session; reload before reading ORM attributes.
    await db.session.refresh(user)
    await db.session.refresh(role_group)

    data = rep.json()
    assert await db.session.get(App, data["id"]) is not None

    assert data["name"] == "Created"
    assert data["description"] == ""

    app_groups = (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == data["id"]))).all()
    assert len(app_groups) == 1

    test_app_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.name == "App-Created-Owners", AppGroup.app_id == data["id"])
        )
    ).first()
    assert test_app_group is not None
    assert test_app_group.is_owner is True

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == test_app_group.id)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.ended_at.is_(None)),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(RoleGroupMap)
            .where(RoleGroupMap.group_id == test_app_group.id)
            .where(RoleGroupMap.role_group_id == role_group.id)
            .where(RoleGroupMap.ended_at.is_(None)),
        )
        == 2
    )


async def test_create_app_with_additional_groups(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    url_for: Any,
) -> None:
    # test bad data
    apps_url = url_for("api-apps.apps")
    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {"name": "Test", "description": "test"},
        ],
    }
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 400

    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {"name": "App-WrongApp-Test", "description": "test"},
        ],
    }
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 400

    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {},
        ],
    }
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 400

    create_group_spy = mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    data = {
        "name": "Created",
        "initial_additional_app_groups": [
            {"name": "App-Created-Test", "description": "test"},
            {"name": "App-Created-Test2"},
        ],
    }

    apps_url = url_for("api-apps.apps")
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 3
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.json()
    assert await db.session.get(App, data["id"]) is not None

    assert data["name"] == "Created"
    assert data["description"] == ""

    app_groups = (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == data["id"]))).all()
    assert len(app_groups) == 3

    test_app_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.name == "App-Created-Owners", AppGroup.app_id == data["id"])
        )
    ).first()
    assert test_app_group is not None
    assert test_app_group.is_owner is True

    test_app_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.name == "App-Created-Test", AppGroup.app_id == data["id"])
        )
    ).first()
    assert test_app_group is not None
    assert test_app_group.description == "test"
    assert test_app_group.is_owner is False


async def test_create_app_with_name_collision(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    app_group: AppGroup,
    url_for: Any,
) -> None:
    app = AppFactory.create()
    app.name = "Test-Staging"
    db.session.add(app)
    await db.session.commit()

    app_group.app_id = app.id
    app_group.name = "App-Test-Staging-Group"
    db.session.add(app_group)
    await db.session.commit()

    create_group_spy = mocker.patch.object(
        okta, "create_group", return_value=Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")

    data = {"name": "Test"}

    apps_url = url_for("api-apps.apps")
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 201
    assert create_group_spy.call_count == 1
    assert add_user_to_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 1

    data = rep.json()
    assert await db.session.get(App, data["id"]) is not None
    assert data["name"] == "Test"

    # Make sure new app doesn't end up with additional app groups from name collision
    app_groups = (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == data["id"]))).all()
    assert len(app_groups) == 1

    # The create-app request staled the original app's identity-map entry on
    # the shared session; reload before reading its id.
    await db.session.refresh(app)

    # Make sure original app still has its app group
    app_groups = (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == app.id))).all()
    assert len(app_groups) == 1


async def test_create_app_additional_group_collision_converts_correct_group(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    url_for: Any,
) -> None:
    # Regression for #505: the additional-app-groups collision branch must convert
    # existing_group, not existing_owner_group. With the bug, no pre-existing owner
    # group leaves existing_owner_group == None, so ModifyGroupType(group=None) raises
    # AttributeError and the request 500s.
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}Payments{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    legacy_name = f"{prefix}Legacy"
    shared_name = f"{prefix}Shared"

    # A pre-existing plain OktaGroup whose name collides with a requested additional
    # app group. No App-Payments-Owners group pre-exists, so with the bug
    # existing_owner_group is None.
    plain_group = OktaGroupFactory.create(name=legacy_name)
    db.session.add(plain_group)
    await db.session.commit()
    plain_group_id = plain_group.id

    # A pre-existing AppGroup attached to a *different* app that also collides — it
    # should be reattached to the new app.
    other_app = AppFactory.create()
    db.session.add(other_app)
    await db.session.commit()
    other_app_group = AppGroupFactory.create(app_id=other_app.id, name=shared_name, is_owner=False)
    db.session.add(other_app_group)
    await db.session.commit()
    other_app_group_id = other_app_group.id

    apps_url = url_for("api-apps.apps")
    rep = await client.post(
        apps_url,
        json={
            "name": "Payments",
            "initial_additional_app_groups": [
                {"name": legacy_name},
                {"name": shared_name},
            ],
        },
    )
    assert rep.status_code == 201
    new_app_id = rep.json()["id"]

    # The plain OktaGroup was converted to an AppGroup owned by the new app.
    converted = await db.session.get(AppGroup, plain_group_id)
    assert converted is not None
    assert converted.app_id == new_app_id
    assert converted.is_owner is False

    # The AppGroup previously attached to another app was reattached to the new app.
    reattached = await db.session.get(AppGroup, other_app_group_id)
    assert reattached is not None
    assert reattached.app_id == new_app_id
    assert reattached.is_owner is False


async def test_get_all_app(client: AsyncClient, db: Db, url_for: Any) -> None:
    apps_url = url_for("api-apps.apps")
    apps = AppFactory.create_batch(10)

    # Prefix all the app names with A so we can query for them easily
    for app in apps:
        app.name = f"A{app.name}"

    db.session.add_all(apps)
    await db.session.commit()

    rep = await client.get(apps_url)
    assert rep.status_code == 200

    results = rep.json()
    for app in apps:
        assert any(u["id"] == app.id for u in results["items"])

    rep = await client.get(apps_url, params={"q": "a"})
    assert rep.status_code == 200

    results = rep.json()
    for app in apps:
        assert any(u["id"] == app.id for u in results["items"])


@pytest.mark.parametrize("app", [False, True], indirect=True)
async def test_create_app_with_and_without_description(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    url_for: Any,
) -> None:
    """Test that apps work with or without descriptions based on REQUIRE_DESCRIPTIONS setting"""
    require_descriptions = settings.REQUIRE_DESCRIPTIONS

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    apps_url = url_for("api-apps.apps")

    # Test creating app without description
    data: dict[str, Any] = {"name": "TestAppNoDesc"}
    rep = await client.post(apps_url, json=data)
    if require_descriptions:
        # Should fail when REQUIRE_DESCRIPTIONS=True
        assert rep.status_code == 400
        response_data = rep.json()
        assert "required" in str(response_data).lower()
    else:
        # Should succeed with backwards compatibility
        assert rep.status_code == 201
        result = rep.json()
        assert result["name"] == "TestAppNoDesc"
        assert result["description"] == ""

    # Test creating app with empty description
    data = {"name": "TestAppEmptyDesc", "description": ""}
    rep = await client.post(apps_url, json=data)
    if require_descriptions:
        # Should fail - empty description fails length validation
        assert rep.status_code == 400
        response_data = rep.json()
        assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()
    else:
        # Should succeed with empty description
        assert rep.status_code == 201
        result = rep.json()
        assert result["name"] == "TestAppEmptyDesc"
        assert result["description"] == ""

    # Test creating app with a description should always succeed
    data = {"name": "TestAppWithDesc", "description": "This has a description"}
    rep = await client.post(apps_url, json=data)
    assert rep.status_code == 201

    result = rep.json()
    assert result["name"] == "TestAppWithDesc"
    assert result["description"] == "This has a description"


@pytest.mark.parametrize("app", [False, True], indirect=True)
async def test_partial_app_update_preserves_description(
    app: FastAPI, client: AsyncClient, db: Db, mocker: MockerFixture, access_app: App, app_group: AppGroup, url_for: Any
) -> None:
    """Test that app updates handle descriptions correctly based on REQUIRE_DESCRIPTIONS setting"""
    require_descriptions = settings.REQUIRE_DESCRIPTIONS

    # Set up the app with a description
    access_app.description = "Original description"
    db.session.add(access_app)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    mocker.patch.object(okta, "update_group")

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)

    # Test updating with empty description
    data = {"name": "UpdatedWithEmpty", "description": ""}
    rep = await client.put(app_url, json=data)
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

    # Reset the app description back to "Original description" for the partial update test
    if not require_descriptions:
        # Need to reset since empty description succeeded above
        data = {"name": "UpdatedWithEmpty", "description": "Original description"}
        rep = await client.put(app_url, json=data)
        assert rep.status_code == 200

    # Test partial update without description should preserve existing description
    data = {"name": "UpdatedName"}
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    result = rep.json()
    assert result["name"] == "UpdatedName"
    assert result["description"] == "Original description"  # Description is preserved

    # Test updating with valid description should succeed
    data = {"name": "UpdatedName2", "description": "Updated description"}
    rep = await client.put(app_url, json=data)
    assert rep.status_code == 200
    result = rep.json()
    assert result["name"] == "UpdatedName2"
    assert result["description"] == "Updated description"


async def test_create_app_fails_when_preexisting_owner_group_is_occupied(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    url_for: Any,
) -> None:
    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}Payments"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    squatted_group = OktaGroupFactory.create(name=owner_group_name)
    db.session.add(squatted_group)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=squatted_group.id, is_owner=True))
    await db.session.commit()

    apps_url = url_for("api-apps.apps")
    rep = await client.post(apps_url, json={"name": "Payments"})
    assert rep.status_code == 409

    assert (
        await db.session.scalars(select(App).where(App.name == "Payments").where(App.deleted_at.is_(None)))
    ).first() is None


async def test_create_app_succeeds_with_empty_preexisting_owner_group(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    url_for: Any,
) -> None:
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}Payments"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    empty_group = OktaGroupFactory.create(name=owner_group_name)
    db.session.add(empty_group)
    await db.session.commit()

    apps_url = url_for("api-apps.apps")
    rep = await client.post(apps_url, json={"name": "Payments"})
    assert rep.status_code == 201

    data = rep.json()
    assert data["name"] == "Payments"
    assert (
        await db.session.scalars(select(App).where(App.name == "Payments").where(App.deleted_at.is_(None)))
    ).first() is not None


async def test_create_app_succeeds_with_members_only_preexisting_owner_group(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    faker: Faker,  # type: ignore[type-arg]
    user: OktaUser,
    url_for: Any,
) -> None:
    db.session.add(user)
    await db.session.commit()

    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group({"id": cast(FakerWithPyStr, faker).pystr()})
    )
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    owner_group_name = (
        f"{AppGroup.APP_GROUP_NAME_PREFIX}Payments"
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    )
    group_with_members = OktaGroupFactory.create(name=owner_group_name)
    db.session.add(group_with_members)
    await db.session.commit()
    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=group_with_members.id, is_owner=False))
    await db.session.commit()

    apps_url = url_for("api-apps.apps")
    rep = await client.post(apps_url, json={"name": "Payments"})
    assert rep.status_code == 201

    data = rep.json()
    assert data["name"] == "Payments"
    assert (
        await db.session.scalars(select(App).where(App.name == "Payments").where(App.deleted_at.is_(None)))
    ).first() is not None


async def test_delete_reserved_access_app_blocked(client: AsyncClient, db: Db, url_for: Any) -> None:
    """The built-in Access app underpins admin auth, so DELETE must refuse
    it even for an admin caller."""
    app_url = url_for("api-apps.app_by_id", app_id=App.ACCESS_APP_RESERVED_NAME)
    rep = await client.delete(app_url)
    assert rep.status_code == 400
    assert "cannot be deleted" in rep.text


async def test_put_app_logs_audit_on_rename(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    url_for: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Flask emitted EventType.app_modify_name on rename; the FastAPI PUT
    handler must continue to do so."""
    mocker.patch.object(okta, "update_group")
    db.session.add(access_app)
    await db.session.commit()
    old_name = access_app.name

    app_url = url_for("api-apps.app_by_id_put", app_id=access_app.id)
    new_name = f"{old_name}Renamed"
    with caplog.at_level("INFO", logger="access.audit"):
        rep = await client.put(app_url, json={"name": new_name})
    assert rep.status_code == 200, rep.text

    audit_messages = [r.getMessage() for r in caplog.records if r.name == "access.audit"]
    assert any("APP_MODIFY_NAME" in m for m in audit_messages), audit_messages
    assert any(old_name in m for m in audit_messages), audit_messages


async def test_post_app_validation_via_http(client: AsyncClient, db: Db, url_for: Any) -> None:
    """Body validation enforced at the HTTP layer (not just Pydantic-level).
    The project's request_validation_handler converts 422 → 400 with the
    RFC 9457 problem-detail envelope."""
    apps_url = url_for("api-apps.apps")

    rep = await client.post(apps_url, json={"name": ""})
    assert rep.status_code == 400
    body = rep.json()
    assert body["status"] == 400
    assert "detail" in body

    rep = await client.post(apps_url, json={"name": "MyApp", "description": "x" * 1025})
    assert rep.status_code == 400

    rep = await client.post(
        apps_url,
        json={"name": "MyApp", "initial_additional_app_groups": [{"name": "wrong-prefix"}]},
    )
    assert rep.status_code == 400


async def test_post_app_require_descriptions_enforced_via_http(
    client: AsyncClient,
    db: Db,
    url_for: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQUIRE_DESCRIPTIONS rejects missing/empty description from a real
    client request, not just Pydantic.model_validate."""
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    apps_url = url_for("api-apps.apps")

    rep = await client.post(apps_url, json={"name": "DescRequired"})
    assert rep.status_code == 400
    rep = await client.post(apps_url, json={"name": "DescRequired", "description": ""})
    assert rep.status_code == 400


async def test_get_apps_q_via_http(client: AsyncClient, db: Db, url_for: Any) -> None:
    """`q` is honored end-to-end on /api/apps."""
    a1 = AppFactory.create(name="ZelaPaymentsApp", description="Handles money flows")
    a2 = AppFactory.create(name="LoggingApp", description="Stores logs")
    db.session.add_all([a1, a2])
    await db.session.commit()

    apps_url = url_for("api-apps.apps")
    rep = await client.get(apps_url, params={"q": "ZelaPayments"})
    assert rep.status_code == 200
    names = [a["name"] for a in rep.json()["items"]]
    assert "ZelaPaymentsApp" in names
    assert "LoggingApp" not in names
