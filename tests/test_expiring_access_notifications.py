from datetime import datetime, timedelta

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.plugins import get_notification_hook
from api.syncer import (
    expiring_access_notifications_owner,
    expiring_access_notifications_role_owner,
    expiring_access_notifications_user,
)
from tests.factories import OktaGroupFactory, OktaUserFactory, RoleGroupFactory


# Test with one user who has two memberships expiring tomorrow
def test_individual_expiring_access_notifications(
    db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    membership1 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.group_id == okta_group.id)
        .first()
    )
    membership2 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.group_id == role_group.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["okta_user_group_members"]) == 2
    assert membership1 in kwargs["okta_user_group_members"]
    assert membership2 in kwargs["okta_user_group_members"]
    assert user == kwargs["user"]
    if datetime.now().weekday() == 4:
        assert kwargs["expiration_datetime"] is None
    else:
        assert expiration_datetime.date() == kwargs["expiration_datetime"].date()
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert okta_group in kwargs["groups"]
    assert role_group in kwargs["groups"]


# Test with one user who has a membership expiring tomorrow and one in a week
def test_individual_expiring_access_notifications_week(
    db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime1 = datetime.now() + timedelta(days=1)
    expiration_datetime2 = datetime.now() + timedelta(weeks=1)

    ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime1, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime2, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    membership1 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.group_id == okta_group.id)
        .first()
    )
    membership2 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.group_id == role_group.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["okta_user_group_members"]) == 2
    assert membership1 in kwargs["okta_user_group_members"]
    assert membership2 in kwargs["okta_user_group_members"]
    assert user == kwargs["user"]
    assert kwargs["expiration_datetime"] is None
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert okta_group in kwargs["groups"]
    assert role_group in kwargs["groups"]


# Test with one user who has one direct membership expiring tomorrow and a role membership for the same group
def test_individual_expiring_direct_with_role(
    db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)
    other_date = datetime.now() + timedelta(days=90)

    ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group, users_added_ended_at=other_date, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group, groups_added_ended_at=other_date, groups_to_add=[okta_group.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 0


# Test with one user who has one direct membership expiring in a week and a role membership for the same group
def test_individual_expiring_direct_with_role_week(
    db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(weeks=1)
    other_date = datetime.now() + timedelta(days=90)

    ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group, users_added_ended_at=other_date, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group, groups_added_ended_at=other_date, groups_to_add=[okta_group.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two groups, each group has a member whose access expires this week
def test_owner_expiring_access_notifications(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group1 = OktaGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    user2 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(user2)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group1.id)
        .filter(OktaUserGroupMember.user_id == user1.id)
        .first()
    )
    membership2 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group2.id)
        .filter(OktaUserGroupMember.user_id == user2.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["group_user_associations"]) == 2
    assert membership1 in kwargs["group_user_associations"]
    assert membership2 in kwargs["group_user_associations"]
    assert kwargs["role_group_associations"] is None
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert group1 in kwargs["groups"]
    assert group2 in kwargs["groups"]
    assert len(kwargs["users"]) == 2
    assert user1 in kwargs["users"]
    assert user2 in kwargs["users"]
    assert kwargs["roles"] is None


# Test with one owner who owns one group, the owner is a member of the group and their access is expiring this week
def test_owner_expiring_access_notifications_owner_only_member(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group, users_added_ended_at=expiration_datetime, members_to_add=[owner.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=group, owners_to_add=[owner.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two groups, each group has a member whose access expires this week
# The owner is also a group member with expiring access but their own access should not be included.
def test_owner_expiring_access_notifications_owner_member(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group1 = OktaGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    user2 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(user2)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id, owner.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group1.id)
        .filter(OktaUserGroupMember.user_id == user1.id)
        .first()
    )
    membership2 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group1.id)
        .filter(OktaUserGroupMember.user_id == owner.id)
        .first()
    )
    membership3 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group2.id)
        .filter(OktaUserGroupMember.user_id == user2.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["group_user_associations"]) == 2
    assert membership1 in kwargs["group_user_associations"]
    assert membership2 not in kwargs["group_user_associations"]
    assert membership3 in kwargs["group_user_associations"]
    assert kwargs["role_group_associations"] is None
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert group1 in kwargs["groups"]
    assert group2 in kwargs["groups"]
    assert len(kwargs["users"]) == 2
    assert user1 in kwargs["users"]
    assert user2 in kwargs["users"]
    assert kwargs["roles"] is None


# Test with one owner who owns two groups, each group has a member whose access expires next week
def test_owner_expiring_access_notifications_week(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group1 = OktaGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    user2 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(user2)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group1.id)
        .filter(OktaUserGroupMember.user_id == user1.id)
        .first()
    )
    membership2 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group2.id)
        .filter(OktaUserGroupMember.user_id == user2.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["group_user_associations"]) == 2
    assert membership1 in kwargs["group_user_associations"]
    assert membership2 in kwargs["group_user_associations"]
    assert kwargs["role_group_associations"] is None
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert group1 in kwargs["groups"]
    assert group2 in kwargs["groups"]
    assert len(kwargs["users"]) == 2
    assert user1 in kwargs["users"]
    assert user2 in kwargs["users"]
    assert kwargs["roles"] is None


# Test with one owner who owns one group, the owner is a member of the group and their access is expiring next week
def test_owner_expiring_access_notifications_owner_only_member_week(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(group)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group, users_added_ended_at=expiration_datetime, members_to_add=[owner.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=group, owners_to_add=[owner.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two groups, each group has a member whose access expires next week
# The owner is also a group member with expiring access but their own access should not be included.
def test_owner_expiring_access_notifications_owner_member_week(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group1 = OktaGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    user2 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(user2)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id, owner.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group1.id)
        .filter(OktaUserGroupMember.user_id == user1.id)
        .first()
    )
    membership2 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group1.id)
        .filter(OktaUserGroupMember.user_id == owner.id)
        .first()
    )
    membership3 = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group2.id)
        .filter(OktaUserGroupMember.user_id == user2.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["group_user_associations"]) == 2
    assert membership1 in kwargs["group_user_associations"]
    assert membership2 not in kwargs["group_user_associations"]
    assert membership3 in kwargs["group_user_associations"]
    assert kwargs["role_group_associations"] is None
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert group1 in kwargs["groups"]
    assert group2 in kwargs["groups"]
    assert len(kwargs["users"]) == 2
    assert user1 in kwargs["users"]
    assert user2 in kwargs["users"]
    assert kwargs["roles"] is None


# Test with one owner who owns a groups, the group has a role member whose access expires this week
def test_owner_expiring_access_notifications_role(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group1 = RoleGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(group=group1, users_added_ended_at=None, members_to_add=[user1.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=group1, groups_added_ended_at=expiration_datetime, groups_to_add=[group2.id], sync_to_okta=False
    ).execute()

    membership1 = (
        RoleGroupMap.query.filter(RoleGroupMap.group_id == group2.id)
        .filter(RoleGroupMap.role_group_id == group1.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["role_group_associations"]) == 1
    assert membership1 in kwargs["role_group_associations"]
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 1
    assert group2 in kwargs["groups"]
    assert kwargs["users"] is None
    assert len(kwargs["roles"]) == 1
    assert group1 in kwargs["roles"]


# Test with one owner who owns two groups, each group has a role member whose access expires next week
def test_owner_expiring_access_notifications_role_week(db: SQLAlchemy, mocker: MockerFixture) -> None:
    role = RoleGroupFactory.create()
    group1 = OktaGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(role)
    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(group=role, users_added_ended_at=None, members_to_add=[user1.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[group1.id, group2.id],
        sync_to_okta=False,
    ).execute()

    membership1 = (
        RoleGroupMap.query.filter(RoleGroupMap.group_id == group1.id)
        .filter(RoleGroupMap.role_group_id == role.id)
        .first()
    )
    membership2 = (
        RoleGroupMap.query.filter(RoleGroupMap.group_id == group2.id)
        .filter(RoleGroupMap.role_group_id == role.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["role_group_associations"]) == 2
    assert membership1 in kwargs["role_group_associations"]
    assert membership2 in kwargs["role_group_associations"]
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 2
    assert group1 in kwargs["groups"]
    assert group2 in kwargs["groups"]
    assert kwargs["users"] is None
    assert len(kwargs["roles"]) == 2
    assert role in kwargs["roles"]


# Test should not renew funtionality for individual notifications
def test_individual_do_not_renew_notification_behavior(
    db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    # Add user to two groups
    ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    # Get the OktaUserGroupMember for the user's membership to role_group
    membership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user.id)
        .filter(OktaUserGroupMember.group_id == role_group.id)
        .first()
    )

    # Mark one membership as 'should_expire'
    ModifyGroupUsers(group=role_group, members_should_expire=[membership.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["groups"]) == 1
    assert okta_group in kwargs["groups"]
    assert user == kwargs["user"]
    if datetime.now().weekday() == 4:
        assert kwargs["expiration_datetime"] is None
    else:
        assert expiration_datetime.date() == kwargs["expiration_datetime"].date()


# Test owner notifications for should not renew funtionality
def test_owner_role_do_not_renew_notification_behavior(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(user1)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group, users_added_ended_at=None, owners_to_add=[owner.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(group=okta_group, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[okta_group.id],
        sync_to_okta=False,
    ).execute()

    # Get the OktaUserGroupMember for the user's membership to role_group
    membership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == user1.id)
        .filter(OktaUserGroupMember.group_id == role_group.id)
        .first()
    )
    # Get RoleGroupMap
    role_membership = (
        RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role_group.id)
        .filter(RoleGroupMap.group_id == okta_group.id)
        .first()
    )

    # Mark user as do not renew
    ModifyGroupUsers(group=role_group, members_should_expire=[membership.id], sync_to_okta=False).execute()

    # Mark role as do not renew
    ModifyRoleGroups(role_group=role_group, groups_should_expire=[role_membership.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two role, each role is a member of a group and its access expires tomorrow
def test_role_owner_expiring_access_notifications_role_tomorrow(db: SQLAlchemy, mocker: MockerFixture) -> None:
    role1 = RoleGroupFactory.create()
    role2 = RoleGroupFactory.create()
    group = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=1)

    db.session.add(role1)
    db.session.add(role2)
    db.session.add(group)
    db.session.add(user1)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(group=role1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role1, groups_added_ended_at=expiration_datetime, groups_to_add=[group.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role2, groups_added_ended_at=expiration_datetime, groups_to_add=[group.id], sync_to_okta=False
    ).execute()

    membership1 = (
        RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role1.id)
        .filter(RoleGroupMap.group_id == group.id)
        .first()
    )
    membership2 = (
        RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role2.id)
        .filter(RoleGroupMap.group_id == group.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_role_owner")

    expiring_access_notifications_role_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["roles"]) == 2
    assert membership1 in kwargs["roles"]
    assert membership2 in kwargs["roles"]
    if datetime.now().weekday() == 4:
        assert kwargs["expiration_datetime"] is None
    else:
        assert expiration_datetime.date() == kwargs["expiration_datetime"].date()


# Test with one owner who owns two role, each role is a member of one or more groups and its
# access expires tomorrow or next week
def test_role_owner_expiring_access_notifications_role_multiple_dates(db: SQLAlchemy, mocker: MockerFixture) -> None:
    role1 = RoleGroupFactory.create()
    role2 = RoleGroupFactory.create()
    group1 = OktaGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=1)
    expiration_datetime2 = datetime.now() + timedelta(weeks=1)

    db.session.add(role1)
    db.session.add(role2)
    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(group=role1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyGroupUsers(group=role2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    ModifyRoleGroups(
        role_group=role1, groups_added_ended_at=expiration_datetime, groups_to_add=[group1.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role2, groups_added_ended_at=expiration_datetime2, groups_to_add=[group1.id], sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role2, groups_added_ended_at=expiration_datetime, groups_to_add=[group2.id], sync_to_okta=False
    ).execute()

    membership1 = (
        RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role1.id)
        .filter(RoleGroupMap.group_id == group1.id)
        .first()
    )
    membership2 = (
        RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role2.id)
        .filter(RoleGroupMap.group_id == group1.id)
        .first()
    )
    membership3 = (
        RoleGroupMap.query.filter(RoleGroupMap.role_group_id == role2.id)
        .filter(RoleGroupMap.group_id == group2.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_role_owner")

    expiring_access_notifications_role_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["roles"]) == 3
    assert membership1 in kwargs["roles"]
    assert membership2 in kwargs["roles"]
    assert membership3 in kwargs["roles"]
    assert kwargs["expiration_datetime"] is None


# Test with an externally managed group and a non-externally managed group. The Access admin should only be notified about
# expiring access for the non-externally managed group
def test_owner_expiring_access_notifications_managed_group_admin(
    db: SQLAlchemy, app: Flask, mocker: MockerFixture
) -> None:
    # Create an externally managed group and an Access managed group
    group1 = OktaGroupFactory.create()
    group1.is_managed = False
    group2 = OktaGroupFactory.create()

    user = OktaUserFactory.create()
    access_owner = OktaUser.query.filter(OktaUser.email == app.config["CURRENT_OKTA_USER_EMAIL"]).first()

    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user)
    db.session.commit()

    ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    membership = (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group2.id)
        .filter(OktaUserGroupMember.user_id == user.id)
        .first()
    )

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    expiring_access_notifications_owner()

    # Access admin should only be notified about expiring access for the Access managed group
    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert kwargs["owner"].id == access_owner.id
    assert len(kwargs["group_user_associations"]) == 1
    assert membership in kwargs["group_user_associations"]
    assert kwargs["role_group_associations"] is None
    # TODO eventually clean this up, leaving for now for backwards compatibility
    assert len(kwargs["groups"]) == 1
    assert group2 in kwargs["groups"]
    assert len(kwargs["users"]) == 1
    assert user in kwargs["users"]
    assert kwargs["roles"] is None
