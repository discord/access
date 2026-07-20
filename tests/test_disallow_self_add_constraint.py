from typing import Any

from httpx import AsyncClient
from pytest_mock import MockerFixture

from fastapi import FastAPI

from sqlalchemy import select
from api.config import settings
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
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.helpers import db_count
from tests.factories import (
    AppTagMapFactory,
    OktaGroupTagMapFactory,
    OktaUserFactory,
    RoleGroupFactory,
    TagFactory,
)


async def test_disallow_self_add_modify_group_users(
    app: FastAPI,
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
    current_user = OktaUserFactory.create()
    app.state.current_user_email = current_user.email

    tags = TagFactory.create_batch(
        3,
        constraints={
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(role_group)
    db.session.add(user)
    db.session.add(current_user)
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

    # drop identity-map state staled by the ops above (expire_on_commit=False)
    db.session.expire_all()
    # reload the objects read below — sync lazy-loads of expired state raise under async
    await db.session.refresh(okta_group)
    await db.session.refresh(current_user)

    # Add non-admin current_user as the owner of okta_group
    await ModifyGroupUsers(group=okta_group, owners_to_add=[current_user.id], sync_to_okta=False).execute()

    # Establish a baseline of user memberships/ownerships
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # Add the current user to the okta group as member
    data: dict[str, Any] = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(current_user)

    # Add the current user to the okta group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(user)

    # Add another user to the okta group as owner and member
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 2

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # reload state expired by the earlier 400 response's rollback
    await db.session.refresh(app_group)
    await db.session.refresh(current_user)

    # Add non-admin current_user as app group owner
    await ModifyGroupUsers(group=app_group, owners_to_add=[current_user.id], sync_to_okta=False).execute()

    # Add the current user to the app group as member
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group.id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as owner
    data = {
        "members_to_add": [],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(current_user)

    # Add the user to the app group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(user)

    # Add another user to the app group as member and owner
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # reload state expired by the earlier 400 response's rollback
    await db.session.refresh(role_group)
    await db.session.refresh(current_user)

    # Add non-admin current_user as app group owner
    await ModifyGroupUsers(group=role_group, owners_to_add=[current_user.id], sync_to_okta=False).execute()

    # Add the current user to the role group as member
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(current_user)

    # Add the user to the role group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(user)

    # Add another user to the role group as member and owner
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 2

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 7
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 8
    )


async def test_disallow_self_add_modify_role_groups(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    current_user = OktaUserFactory.create()
    app.state.current_user_email = current_user.email

    role_groups = RoleGroupFactory.create_batch(2)

    tags = TagFactory.create_batch(
        3,
        constraints={
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add_all(role_groups)
    db.session.add(user)
    db.session.add(current_user)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_groups[0], members_to_add=[current_user.id], owners_to_add=[], sync_to_okta=False
    ).execute()

    await ModifyGroupUsers(
        group=role_groups[1], members_to_add=[user.id], owners_to_add=[], sync_to_okta=False
    ).execute()

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[0].id)
    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_groups[0].id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_groups[1].id, tag_id=tags[2].id)
    app_tag_map = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[1].id)
    app_tag_map2 = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id)

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    # Add current_user as owner of role_groups[0]
    await ModifyGroupUsers(group=okta_group, owners_to_add=[current_user.id], sync_to_okta=False).execute()

    # Add the current user role group as a member to the okta group
    data: dict[str, Any] = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    current_user_role_url = url_for("api-roles.role_members_by_id", role_id=role_groups[0].id)
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(okta_group)

    # Add the current user role group as a owner to the okta group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a member and owner to the okta group
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # reload state expired by the 400 response's rollback
    await db.session.refresh(okta_group)
    await db.session.refresh(role_groups[1])

    # Add the user role group as a member to the okta group
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    user_role_url = url_for("api-roles.role_members_by_id", role_id=role_groups[1].id)
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a owner to the okta group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a member and owner to the okta group
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # reload state expired by the earlier 400 response's rollback
    await db.session.refresh(app_group)
    await db.session.refresh(current_user)

    # Add current_user as owner of the app group
    await ModifyGroupUsers(group=app_group, owners_to_add=[current_user.id], sync_to_okta=False).execute()

    # Add the current user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a owner to the app group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(app_group)

    # Add the current user role group as a member and owner to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    # reload state expired by the 400 response's rollback
    await db.session.refresh(app_group)

    # Add the user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a owner to the app group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a member and owner to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )


# Admin should be allowed to bypass the constraints in all cases
async def test_disallow_self_add_admin_modify_group_users(
    app: FastAPI,
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
    current_user = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    tags = TagFactory.create_batch(
        3,
        constraints={
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
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

    # Establish a baseline of user memberships/ownerships
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )

    # Add the current user to the okta group as member
    data: dict[str, Any] = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )

    # Add the current user to the okta group as owner
    data = {
        "members_to_add": [],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
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
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user to the okta group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user to the okta group as owner and member
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user to the app group as member
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group.id)
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
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as owner
    data = {
        "members_to_add": [],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as member and owner
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user to the role group as member
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 2
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 8
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user to the role group as owner
    data = {
        "members_to_add": [],
        "owners_to_add": [current_user.id],
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
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 8
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 7
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the role group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 8
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 7
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the role group as member and owner
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = await client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 11
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 10
    )


# Admin should be allowed to bypass the constraints in all cases
async def test_disallow_self_add_admin_modify_role_groups(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    current_user = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    role_groups = RoleGroupFactory.create_batch(2)

    tags = TagFactory.create_batch(
        3,
        constraints={
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        },
    )
    tags[1].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add_all(role_groups)
    db.session.add(user)
    await db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_groups[0], members_to_add=[current_user.id], owners_to_add=[], sync_to_okta=False
    ).execute()

    await ModifyGroupUsers(
        group=role_groups[1], members_to_add=[user.id], owners_to_add=[], sync_to_okta=False
    ).execute()

    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[0].id)
    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_groups[0].id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=role_groups[1].id, tag_id=tags[2].id)
    app_tag_map = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[1].id)
    app_tag_map2 = await AppTagMapFactory.create_async(app_id=access_app.id, tag_id=tags[2].id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id)
    await OktaGroupTagMapFactory.create_async(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id)

    add_user_to_group_spy = mocker.patch.object(okta, "add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(okta, "remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    # Add the current user role group as a member to the okta group
    data: dict[str, Any] = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    current_user_role_url = url_for("api-roles.role_members_by_id", role_id=role_groups[0].id)
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 0
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 0
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a owner to the okta group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a member and owner to the okta group
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a member to the okta group
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    user_role_url = url_for("api-roles.role_members_by_id", role_id=role_groups[1].id)
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 1
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a owner to the okta group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a member and owner to the okta group
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 5
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 2
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a owner to the app group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the current user role group as a member and owner to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 6
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )

    # Add the user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 7
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 3
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 3
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a owner to the app group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 7
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user role group as a member and owner to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = await client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(False), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 7
    )
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember).where(
                OktaUserGroupMember.is_owner.is_(True), OktaUserGroupMember.ended_at.is_(None)
            ),
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(False), RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )
    assert (
        await db_count(
            db.session, select(RoleGroupMap).where(RoleGroupMap.is_owner.is_(True), RoleGroupMap.ended_at.is_(None))
        )
        == 4
    )


async def test_disallow_self_add_membership_blocks_direct_modify_group_users(
    db: Db,
    mocker: MockerFixture,
    okta_group: OktaGroup,
) -> None:
    """Operation-level guard: a non-admin self-adding as a member via
    `ModifyGroupUsers(...).execute()` on a group tagged
    `DISALLOW_SELF_ADD_MEMBERSHIP` is blocked, with nothing added. The router
    path is covered above; this drives the operation directly, which is where
    the membership id list feeds `CheckForSelfAdd`."""
    current_user = OktaUserFactory.create()
    tag = TagFactory.create(
        constraints={
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        },
    )
    db.session.add_all([okta_group, current_user, tag])
    await db.session.commit()
    await OktaGroupTagMapFactory.create_async(group_id=okta_group.id, tag_id=tag.id)

    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")

    group_id = okta_group.id
    user_id = current_user.id
    db.session.expire_all()

    # Non-admin current_user attempts to add themself as a member.
    await ModifyGroupUsers(
        group=group_id,
        members_to_add=[user_id],
        current_user_id=user_id,
        sync_to_okta=False,
    ).execute()

    # The constraint blocked it: no active membership was created.
    assert (
        await db_count(
            db.session,
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.user_id == user_id)
            .where(OktaUserGroupMember.ended_at.is_(None)),
        )
        == 0
    )
