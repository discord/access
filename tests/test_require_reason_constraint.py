from typing import Any

from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import inspect as sa_inspect, select

from api.extensions import Db
from api.models import (
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    Tag,
)
from api.operations import CreateAccessRequest, ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import AppTagMapFactory, OktaGroupTagMapFactory, TagFactory
from tests.helpers import db_count


async def _reload(db: Db, obj: Any) -> Any:
    """Re-fetch an ORM instance as a live session object, whether it is
    expired-attached or detached.

    These tests share one session and reuse fixtures across many approvals. Two
    things can invalidate a held instance between requests: the router's
    `db.expire_all()` (expired-but-attached — reading `obj.id` would then trigger
    sync IO under the async session) and an approve's deferred completion
    notification, which `expunge`s its payload (requester/group → detached, so a
    plain `refresh` fails). `inspect(...).identity` reads the primary key from
    instance state without IO, so a `get` works for both."""
    return await db.session.get(type(obj), sa_inspect(obj).identity[0])


async def test_require_reason_modify_group_users(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    tags = TagFactory.batch(
        3,
        constraints={
            Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
            Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: False,
        Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
        Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    await ModifyRoleGroups(
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

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[0].id)
    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_group.id, tag_id=tags[2].id)
    app_tag_map = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[1].id)
    app_tag_map2 = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id)

    # Store IDs before requests — constraint-rejection (400) paths roll the
    # shared session back, expiring instances; expired attributes cannot
    # lazy-load under the async session.
    user_id = user.id
    okta_group_id = okta_group.id
    app_group_id = app_group.id
    role_group_id = role_group.id

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships with a created reason
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # drop identity-map state staled by the ops above (expire_on_commit=False)
    db.session.expire_all()

    # Add the user to the okta group as member without a reason
    data: dict[str, Any] = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group_id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # Add the user to the okta group as owner without a reason
    data = {
        "members_to_add": [],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the okta group as member and owner without a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    # Add the user to the okta group as member and owner with a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as member without a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group_id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as owner without a reason
    data = {
        "members_to_add": [],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    # Add the user to the app group as member and owner without a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    # Add the user to the app group as member and owner with a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the role group as member without a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group_id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # Add the user to the role group as owner without a reason
    data = {
        "members_to_add": [],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the role group as member and owner without a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    # Add the user to the role group as member and owner with a reason
    data = {
        "members_to_add": [user_id],
        "owners_to_add": [user_id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 10
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )


async def test_require_reason_modify_role_groups(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    tags = TagFactory.batch(
        3,
        constraints={
            Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
            Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: False,
        Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
        Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    await ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[], sync_to_okta=False).execute()

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[0].id)
    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_group.id, tag_id=tags[2].id)
    app_tag_map = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[1].id)
    app_tag_map2 = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id)

    # Store IDs before requests — constraint-rejection (400) paths roll the
    # shared session back, expiring instances; expired attributes cannot
    # lazy-load under the async session.
    okta_group_id = okta_group.id
    app_group_id = app_group.id
    role_group_id = role_group.id

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships with a created reason
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    # Add the role group as a member to the okta group without a reason
    data: dict[str, Any] = {
        "groups_to_add": [okta_group_id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group_id)
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    # Add the role group as a owner to the okta group without a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member and owner to the okta group without a reason
    data = {
        "groups_to_add": [okta_group_id],
        "owner_groups_to_add": [okta_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member to the okta group with a reason
    data = {
        "groups_to_add": [okta_group_id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a owner to the okta group with a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member and owner to the okta group with a reason
    data = {
        "groups_to_add": [okta_group_id],
        "owner_groups_to_add": [okta_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member to the app group without a reason
    data = {
        "groups_to_add": [app_group_id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a owner to the app group without a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    # Add the role group as a member and owner to the app group without a reason
    data = {
        "groups_to_add": [app_group_id],
        "owner_groups_to_add": [app_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    # Add the role group as a member to the app group with a reason
    data = {
        "groups_to_add": [app_group_id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a owner to the app group with a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member and owner to the app group with a reason
    data = {
        "groups_to_add": [app_group_id],
        "owner_groups_to_add": [app_group_id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = await client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )


async def test_require_reason_approve_access_request(
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    tags = TagFactory.batch(
        3,
        constraints={
            Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
            Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: False,
        Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
        Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    await ModifyRoleGroups(
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

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[0].id)
    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_group.id, tag_id=tags[2].id)
    app_tag_map = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[1].id)
    app_tag_map2 = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id)

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships with a created reason
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # Approve an access request to the okta group as member and approve without a reason
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    db.session.expire_all()
    # reload async — sync attribute access cannot lazy-load the expired
    # instance under the async session
    assert access_request is not None
    await db.session.refresh(access_request)

    data = {"approved": True, "reason": ""}

    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # Approve an access request to the okta group as owner and approve without a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    okta_group = await _reload(db, okta_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the okta group as member and approve with a reason.
    # The PUT above ran an approval whose deferred completion notification
    # expunged its payload (requester=user, group=okta_group) from the shared
    # session, so re-fetch them (a plain refresh would fail — they're detached,
    # not merely expired) before the operation reads their attributes.
    user = await _reload(db, user)
    okta_group = await _reload(db, okta_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": "approval reason"}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the okta group as owner and approve with a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    okta_group = await _reload(db, okta_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the app group as member and approve without a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    app_group = await _reload(db, app_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": ""}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the app group as owner and approve without a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    app_group = await _reload(db, app_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    # Approve an access request to the app group as member and approve with a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    app_group = await _reload(db, app_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": "approval reason"}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the app group as owner and approve with a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    app_group = await _reload(db, app_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the role group as member and approve without a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    role_group = await _reload(db, role_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": ""}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # Approve an access request to the role group as owner and approve without a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    role_group = await _reload(db, role_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the role group as member and approve with a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    role_group = await _reload(db, role_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": "approval reason"}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 2
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 9
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the role group as owner and approve with a reason
    # the PUT above expired the shared session (router db.expire_all());
    # reload instances async before the operation reads their attributes
    user = await _reload(db, user)
    role_group = await _reload(db, role_group)
    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 10
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
