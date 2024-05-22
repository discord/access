from typing import Any

from flask import url_for
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
from api.operations import CreateAccessRequest, ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import TagFactory


def test_require_reason_modify_group_users(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
) -> None:
    tags = TagFactory.create_batch(
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
        sync_to_okta=False,
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
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships with a created reason
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    # Add the user to the okta group as member without a reason
    data: dict[str, Any] = {
        "members_to_add": [user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    # Add the user to the okta group as owner without a reason
    data = {
        "members_to_add": [],
        "owners_to_add": [user.id],
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
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the okta group as member and owner without a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    # Add the user to the okta group as member and owner with a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "created_reason": "test reason",
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
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as member without a reason
    data = {
        "members_to_add": [user.id],
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
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the app group as owner without a reason
    data = {
        "members_to_add": [],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    # Add the user to the app group as member and owner without a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    # Add the user to the app group as member and owner with a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "created_reason": "test reason",
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
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the role group as member without a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    # Add the user to the role group as owner without a reason
    data = {
        "members_to_add": [],
        "owners_to_add": [user.id],
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
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 1

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the user to the role group as member and owner without a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    # Add the user to the role group as member and owner with a reason
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 3
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 10
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )


def test_require_reason_modify_role_groups(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
) -> None:
    tags = TagFactory.create_batch(
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
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[], sync_to_okta=False).execute()

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
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships with a created reason
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 0
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0

    # Add the role group as a member to the okta group without a reason
    data: dict[str, Any] = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    role_url = url_for("api-roles.role_members_by_id", role_id=role_group.id)
    rep = client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 0
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0

    # Add the role group as a owner to the okta group without a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 0
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 1

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member and owner to the okta group without a reason
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 0
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 1

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member to the okta group with a reason
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 1
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 1

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a owner to the okta group with a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 2
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member and owner to the okta group with a reason
    data = {
        "groups_to_add": [okta_group.id],
        "owner_groups_to_add": [okta_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 2
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member to the app group without a reason
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 2
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 1

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a owner to the app group without a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 2
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 1

    # Add the role group as a member and owner to the app group without a reason
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 2
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 1

    # Add the role group as a member to the app group with a reason
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 3
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a owner to the app group with a reason
    data = {
        "groups_to_add": [],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 4
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Add the role group as a member and owner to the app group with a reason
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
        "groups_to_remove": [],
        "owner_groups_to_remove": [],
        "created_reason": "test reason",
    }
    rep = client.put(role_url, json=data)
    assert rep.status_code == 200

    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason != "", RoleGroupMap.ended_at.is_(None)).count() == 4
    assert RoleGroupMap.query.filter(RoleGroupMap.created_reason == "", RoleGroupMap.ended_at.is_(None)).count() == 0


def test_require_reason_approve_access_request(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    access_app: App,
    app_group: AppGroup,
    okta_group: OktaGroup,
    role_group: RoleGroup,
    user: OktaUser,
) -> None:
    tags = TagFactory.create_batch(
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
        sync_to_okta=False,
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
    remove_user_from_group_spy = mocker.patch.object(okta, "async_remove_user_from_group")
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(okta, "async_remove_owner_from_group")

    # Establish a baseline of user memberships/ownerships with a created reason
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    # Approve an access request to the okta group as member and approve without a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": ""}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    # Approve an access request to the okta group as owner and approve without a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the okta group as member and approve with a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": "approval reason"}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the okta group as owner and approve with a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the app group as member and approve without a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": ""}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the app group as owner and approve without a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    # Approve an access request to the app group as member and approve with a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": "approval reason"}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the app group as owner and approve with a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=app_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the role group as member and approve without a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": ""}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 400

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )

    # Approve an access request to the role group as owner and approve without a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the role group as member and approve with a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=False,
        request_reason="test reason",
    ).execute()

    data = {"approved": True, "reason": "approval reason"}

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 3
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 2
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 9
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 2
    )

    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()

    # Approve an access request to the role group as owner and approve with a reason
    access_request = CreateAccessRequest(
        requester_user=user,
        requested_group=role_group,
        request_ownership=True,
        request_reason="test reason",
    ).execute()

    assert access_request is not None
    access_request_url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = client.put(access_request_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason != "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 10
    )
    assert (
        OktaUserGroupMember.query.filter(
            OktaUserGroupMember.created_reason == "", OktaUserGroupMember.ended_at.is_(None)
        ).count()
        == 1
    )
