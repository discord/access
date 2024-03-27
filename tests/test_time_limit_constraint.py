
from datetime import UTC, datetime, timedelta
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
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import TagFactory

SEVEN_DAYS_IN_SECONDS = 7 * 24 * 60 * 60
THREE_DAYS_IN_SECONDS = 3 * 24 * 60 * 60
ONE_DAY_IN_SECONDS = 24 * 60 * 60

def test_time_limit_modify_group_users(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # Set primary tag constraint time limit to 3 days
    tags = TagFactory.create_batch(3, constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS
    })
    tags[1].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
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

    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[1].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[0].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[0].id, app_tag_map_id=app_tag_map.id))
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

    # Establish a baseline where a user shouldn't be expiring in the next 8 days
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now(),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 0
    )

    # Add the user to the app group as member and owner for longer than the time limit constraint
    data: dict[str, Any] = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=7),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    # user should only be added to the app group for 3 days (time limit constraint)
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 0
    )

    # Add the user to the app group as member and owner for less than the time limit constraint
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=1),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 1
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 1
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    # user should only be added to the app group group for 1 days (less than time limit constraint)
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now(),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=2))).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 0
    )

    # Remove the user from the app group as member and owner
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [],
        "owners_to_add": [],
        "members_to_remove": [user.id],
        "owners_to_remove": [user.id],
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=app_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 1
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 1

    data = rep.get_json()
    assert len(data["members"]) == 0
    assert len(data["owners"]) == 0

    # Add the role group as a member and owner of the okta group
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
    assert add_user_to_group_spy.call_count == 0
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 0
    assert remove_owner_from_group_spy.call_count == 0
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )

    data = rep.get_json()
    assert len(data["groups_in_role"]) == 1
    assert data["groups_in_role"][0] == okta_group.id
    assert len(data["groups_owned_by_role"]) == 1
    assert data["groups_owned_by_role"][0] == okta_group.id

    # Add the user to the group as member and owner for longer than the time limit constraint
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=7),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 2
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    # user should only be added to the role and okta group for 3 days (time limit constraint)
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 0
    )

    # Add the user to the role as member and owner for less than the time limit constraint
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "members_to_add": [user.id],
        "owners_to_add": [user.id],
        "members_to_remove": [],
        "owners_to_remove": [],
        "users_added_ending_at": datetime.now(UTC) + timedelta(days=1),
    }
    group_url = url_for("api-groups.group_members_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert add_user_to_group_spy.call_count == 2
    assert remove_user_from_group_spy.call_count == 0
    assert add_owner_to_group_spy.call_count == 2
    assert remove_owner_from_group_spy.call_count == 0

    data = rep.get_json()
    assert len(data["members"]) == 1
    assert len(data["owners"]) == 1

    # user should only be added to the role and okta group for 1 days (less than time limit constraint)
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now(),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=2))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 0
    )

def test_time_limit_modify_role_groups(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # Set primary tag constraint time limit to 3 days
    tags = TagFactory.create_batch(3, constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS
    })
    tags[1].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
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

    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[1].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[0].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[0].id, app_tag_map_id=app_tag_map.id))
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id))
    db.session.commit()

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()

    add_user_to_group_spy = mocker.patch.object(okta, "async_add_user_to_group")
    remove_user_from_group_spy = mocker.patch.object(
        okta, "async_remove_user_from_group"
    )
    add_owner_to_group_spy = mocker.patch.object(okta, "async_add_owner_to_group")
    remove_owner_from_group_spy = mocker.patch.object(
        okta, "async_remove_owner_from_group"
    )

    # Establish a base line of tag, user, and role mappings
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(db.or_(RoleGroupMap.ended_at.is_(None),
                                         RoleGroupMap.ended_at > db.func.now())).count()
        == 0
    )


    # Add the role group as a member and owner of the okta group, for an unlimited time
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
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    # Add the role group as a member and owner of the app group, for an unlimited time
    add_user_to_group_spy.reset_mock()
    remove_user_from_group_spy.reset_mock()
    add_owner_to_group_spy.reset_mock()
    remove_owner_from_group_spy.reset_mock()
    data = {
        "groups_to_add": [app_group.id],
        "owner_groups_to_add": [app_group.id],
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
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

def test_time_limit_modify_group_type(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture,access_app: App, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # Set primary tag constraint time limit to 3 days
    tags = TagFactory.create_batch(3, constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS
    })
    tags[1].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
    }
    db.session.add_all(tags)
    db.session.add(okta_group)
    db.session.add(access_app)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[2].id))
    db.session.add_all([AppTagMap(app_id=access_app.id, tag_id=tag.id) for tag in tags])
    db.session.commit()

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[
            okta_group.id,
        ],
        owner_groups_to_add=[
            okta_group.id,
        ],
        sync_to_okta=False
    ).execute()
    okta_group_id = okta_group.id
    role_group_id = role_group.id

    # Establish a base line of tag, user, and role mappings
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 3
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 2
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    update_group_spy = mocker.patch.object(okta, "update_group")

    # Update the okta group to be an app group which should affect and limit it's role group mapping
    data = {
        "type": "app_group",
        "app_id": access_app.id,
        "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated-Okta",
        "description": "new description",
    }

    group_url = url_for("api-groups.group_by_id", group_id=okta_group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    ret_data = rep.get_json()
    assert ret_data["type"] == data["type"]
    assert ret_data["name"] == data["name"]
    assert ret_data["description"] == data["description"]
    assert ret_data["id"] == okta_group_id
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 3
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 5
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )

    # Update the role group to be an app group which should affect and also limit it's role group mapping
    update_group_spy.reset_mock()
    data["name"] = data["name"].replace("Updated-Okta", "Updated-Role")

    group_url = url_for("api-groups.group_by_id", group_id=role_group_id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    ret_data = rep.get_json()
    assert ret_data["type"] == data["type"]
    assert ret_data["name"] == data["name"]
    assert ret_data["description"] == data["description"]
    assert ret_data["id"] == role_group_id
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 3
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 8
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(db.or_(RoleGroupMap.ended_at.is_(None),
                                         RoleGroupMap.ended_at > db.func.now())).count()
        == 0
    )


def test_time_limit_modify_group_tags(client: FlaskClient, db: SQLAlchemy, access_app: App, app_group: AppGroup, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # Set primary tag constraint time limit to 7 days initially
    tags = TagFactory.create_batch(3, constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
    })
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
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

    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[0].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[1].id))
    db.session.add(OktaGroupTagMap(group_id=role_group.id, tag_id=tags[2].id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tags[0].id)
    db.session.add(app_tag_map)
    app_tag_map2 = AppTagMap(app_id=access_app.id, tag_id=tags[2].id)
    db.session.add(app_tag_map2)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[0].id, app_tag_map_id=app_tag_map.id))
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tags[2].id, app_tag_map_id=app_tag_map2.id))
    db.session.commit()

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
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

    # Establish a baseline of user and role mappings
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 6
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 2
    )

    # Update the primary tag constraint time limit to 3 days
    data = {
        "name": tags[0].name,
        "description": tags[0].description,
        "enabled": True,
        "constraints": {
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        },
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tags[0].id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == tags[0].name
    assert data["description"] == tags[0].description
    assert data["enabled"] is True
    assert data["constraints"] == {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
    }
    assert data["id"] == tags[0].id

    # The primary tag should reduce the user and role mappings to three days
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 0
    )

    # Update the disabled tag to enabled
    data = {
        "name": tags[2].name,
        "description": tags[2].description,
        "enabled": True,
        "constraints": {
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        },
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tags[2].id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == tags[2].name
    assert data["description"] == tags[2].description
    assert data["enabled"] is True
    assert data["constraints"] == {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
    }
    assert data["id"] == tags[2].id

    # The enabled tag should reduce the user and role mappings to one day
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now(),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=2))).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2))).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > db.func.now(),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=2))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2))).count()
        == 0
    )

    # Update the last tag to be disabled
    data = {
        "name": tags[2].name,
        "description": tags[2].description,
        "enabled": False,
        "constraints": {
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        },
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tags[2].id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.get_json()
    assert data["name"] == tags[2].name
    assert data["description"] == tags[2].description
    assert data["enabled"] is False
    assert data["constraints"] == {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
    }
    assert data["id"] == tags[2].id

    # The disabled tag should have no effect on the user and role mappings
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now(),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=2))).count()
        == 6
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2))).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > db.func.now(),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=2))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2))).count()
        == 0
    )

def test_time_limit_add_group_tags(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, okta_group: OktaGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # Set primary tag constraint time limit to 3 days
    tags = TagFactory.create_batch(3, constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS
    })
    tags[1].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
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

    ModifyGroupUsers(
        group=okta_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[
            okta_group.id,
            app_group.id
        ],
        owner_groups_to_add=[
            okta_group.id,
            app_group.id
        ],
        sync_to_okta=False
    ).execute()

    # Establish a base line of tag, user, and role mappings
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now()).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 9
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > db.func.now()).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 4
    )

    update_group_spy = mocker.patch.object(okta, "update_group")

    # Add the primary and disabled tag to the okta group
    data: dict[str, Any] = {
        "type": okta_group.type,
        "name": okta_group.name,
        "description": okta_group.description,
        "tags_to_add": [tags[0].id, tags[2].id],
    }
    group_url = url_for("api-groups.group_by_id", group_id=okta_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 2

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 5
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    update_group_spy.reset_mock()

    # Add the secondary and disabled tag to the role group
    data = {
        "type": role_group.type,
        "name": role_group.name,
        "description": role_group.description,
        "tags_to_add": [tags[1].id, tags[2].id],
    }
    group_url = url_for("api-groups.group_by_id", group_id=role_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 4

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    update_group_spy.reset_mock()

    # Add the secondary and disabled tag to the app group
    data = {
        "type": app_group.type,
        "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Updated",
        "description": app_group.description,
        "tags_to_add": [tags[1].id, tags[2].id],
        "app_id": access_app.id,
    }
    group_url = url_for("api-groups.group_by_id", group_id=app_group.id)
    rep = client.put(group_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 6

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 1
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=6)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=8))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )


def test_time_limit_add_app_tags(client: FlaskClient, db: SQLAlchemy, mocker: MockerFixture, access_app: App, app_group: AppGroup, role_group: RoleGroup, user: OktaUser) -> None:
    # Set primary tag constraint time limit to 3 days
    tags = TagFactory.create_batch(3, constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: THREE_DAYS_IN_SECONDS
    })
    tags[1].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: SEVEN_DAYS_IN_SECONDS,
    }
    tags[2].enabled = False
    tags[2].constraints = {
        Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
        Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY_IN_SECONDS,
    }
    db.session.add_all(tags)
    db.session.add(access_app)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    ModifyGroupUsers(
        group=app_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_to_add=[
            app_group.id
        ],
        owner_groups_to_add=[
            app_group.id
        ],
        sync_to_okta=False
    ).execute()

    # Establish a base line of tag, user, and role mappings
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 0
    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 0
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > db.func.now()).count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 7
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > db.func.now()).count()
        == 0
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 2
    )

    update_group_spy = mocker.patch.object(okta, "update_group")

    # Add the primary and disabled tag to the app
    data = {
        "name": "Updated",
        "description": "new description",
        "tags_to_add": [tags[0].id, tags[2].id]
    }

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 1

    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 2
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 2
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )

    update_group_spy.reset_mock()

    # Remove the primary tag, and add the second and disabled tag to the app
    data = {
        "name": "Updated",
        "description": "new description",
        "tags_to_remove": [tags[0].id],
        "tags_to_add": [tags[1].id, tags[2].id]
    }

    app_url = url_for("api-apps.app_by_id", app_id=access_app.id)
    rep = client.put(app_url, json=data)
    assert rep.status_code == 200
    assert update_group_spy.call_count == 0

    assert OktaGroupTagMap.query.filter(OktaGroupTagMap.ended_at.is_(None)).count() == 2
    assert AppTagMap.query.filter(AppTagMap.ended_at.is_(None)).count() == 2
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                         OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 4
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.ended_at.is_(None)).count()
        == 3
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at > (datetime.now(UTC) + timedelta(days=2)),
                                  RoleGroupMap.ended_at < (datetime.now(UTC) + timedelta(days=4))).count()
        == 2
    )
    assert (
        RoleGroupMap.query.filter(RoleGroupMap.ended_at.is_(None)).count()
        == 0
    )
