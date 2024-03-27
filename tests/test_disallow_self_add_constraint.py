
from typing import Any

from flask import Flask, url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import (
    App,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    Tag,
)
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import OktaUserFactory, RoleGroupFactory, TagFactory

def test_disallow_self_add_modify_group_users(app: Flask, client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    current_user = OktaUserFactory.create()
    app.config["CURRENT_OKTA_USER_EMAIL"] = current_user.email

    tags = TagFactory.create_batch(3, constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
    })
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
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

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
        sync_to_okta=False
    ).execute()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[2].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[2].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[1].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id))
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id))
    db.session.commit()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(
        okta, "async_remove_user_from_group"
    )
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(
        okta, "async_remove_owner_from_group"
    )

    # Add non-admin current_user as the owner of okta_group
    ModifyGroupUsers(
        group=okta_group,
        owners_to_add=[current_user.id],
        sync_to_okta=False
    ).execute()

    # Establish a baseline of user memberships/ownerships
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )

    # Add the current user to the okta group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )

    # Add another user to the okta group as owner and member
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add non-admin current_user as app group owner
    ModifyGroupUsers(
        group=app_group,
        owners_to_add=[current_user.id],
        sync_to_okta=False
    ).execute()

    # Add the current user to the app group as member
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )

    # Add the user to the app group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )

    # Add another user to the app group as member and owner
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add non-admin current_user as app group owner
    ModifyGroupUsers(
        group=role_group,
        owners_to_add=[current_user.id],
        sync_to_okta=False
    ).execute()

    # Add the current user to the role group as member
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )

    # Add the user to the role group as member and owner
    data = {
        "members_to_add": [current_user.id],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )

    # Add another user to the role group as member and owner
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 7
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 8
    )

def test_disallow_self_add_modify_role_groups(app: Flask, client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, user: OktaUser) -> None:
    current_user = OktaUserFactory.create()
    app.config["CURRENT_OKTA_USER_EMAIL"] = current_user.email

    role_groups = RoleGroupFactory.create_batch(2)

    tags = TagFactory.create_batch(3, constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
    })
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
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(
        group=role_groups[0],
        members_to_add=[current_user.id],
        owners_to_add=[],
        sync_to_okta=False
    ).execute()

    ModifyGroupUsers(
        group=role_groups[1],
        members_to_add=[user.id],
        owners_to_add=[],
        sync_to_okta=False
    ).execute()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[2].id))
    db.session.add(OktaGroupTagMap(group_id=role_groups[0].id, tag_id=tags[2].id))
    db.session.add(OktaGroupTagMap(group_id=role_groups[1].id, tag_id=tags[2].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[1].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id))
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id))
    db.session.commit()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(
        okta, "async_remove_user_from_group"
    )
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(
        okta, "async_remove_owner_from_group"
    )

    # Establish a baseline of user memberships/ownerships
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )

    # Add current_user as owner of role_groups[0]
    ModifyGroupUsers(
        group=okta_group,
        owners_to_add=[current_user.id],
        sync_to_okta=False
    ).execute()

    # Add the current user role group as a member to the okta group
    data: dict[str, Any] = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    current_user_role_url = url_for("api-roles.role_members_by_id", role_id=role_groups[0].id)
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )

    # Add the current user role group as a owner to the okta group
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add current_user as owner of the app group
    ModifyGroupUsers(
        group=app_group,
        owners_to_add=[current_user.id],
        sync_to_okta=False
    ).execute()

    # Add the current user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    # Add the current user role group as a member and owner to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    # Add the user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )

# Admin should be allowed to bypass the constraints in all cases
def test_disallow_self_add_admin_modify_group_users(app: Flask, client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    current_user = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    tags = TagFactory.create_batch(3, constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
    })
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
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

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
        sync_to_okta=False
    ).execute()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[2].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[2].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[1].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id))
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id))
    db.session.commit()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(
        okta, "async_remove_user_from_group"
    )
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(
        okta, "async_remove_owner_from_group"
    )

    # Establish a baseline of user memberships/ownerships
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 0
    )

    # Add the current user to the okta group as owner
    data = {
        "members_to_add": [],
        "owners_to_add": [current_user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 2
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 8
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 8
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 8
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
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
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 2
    assert len(data["owners"]) == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 11
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 10
    )


# Admin should be allowed to bypass the constraints in all cases
def test_disallow_self_add_admin_modify_role_groups(app: Flask, client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, user: OktaUser) -> None:
    current_user = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    role_groups = RoleGroupFactory.create_batch(2)

    tags = TagFactory.create_batch(3, constraints = {
        Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
    })
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
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(
        group=role_groups[0],
        members_to_add=[current_user.id],
        owners_to_add=[],
        sync_to_okta=False
    ).execute()

    ModifyGroupUsers(
        group=role_groups[1],
        members_to_add=[user.id],
        owners_to_add=[],
        sync_to_okta=False
    ).execute()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tags[2].id))
    db.session.add(OktaGroupTagMap(group_id=role_groups[0].id, tag_id=tags[2].id))
    db.session.add(OktaGroupTagMap(group_id=role_groups[1].id, tag_id=tags[2].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[1].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[1].id, app_tag_map_id=app_tag_map.id))
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id))
    db.session.commit()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(
        okta, "async_remove_user_from_group"
    )
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(
        okta, "async_remove_owner_from_group"
    )

    # Establish a baseline of user memberships/ownerships
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(current_user_role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 3
    )

    # Add the user role group as a member to the app group
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 7
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 7
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
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
    rep = client.put(user_role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(False),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 7
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.is_owner.is_(True),
                                         OktaUserGroupMember.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(False),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 4
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.is_owner.is_(True),
                                  RoleGroupMap.ended_at.is_(None)).count()
        == 4
    )