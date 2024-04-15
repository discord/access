from datetime import datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import OktaGroup, OktaUser, RoleGroup
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.plugins import get_notification_hook
from api.syncer import expiring_access_notifications_owner, expiring_access_notifications_user
from tests.factories import OktaGroupFactory, OktaUserFactory, RoleGroupFactory


# Test with one user who has two memberships expiring tomorrow
def test_individual_expiring_access_notifications(db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    ModifyGroupUsers(
        group=okta_group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_user"
    )

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 2
    assert okta_group in kwargs['groups']
    assert role_group in kwargs['groups']
    assert user == kwargs['user']
    if datetime.now().weekday() == 4:
        assert kwargs['expiration_datetime'] is None
    else:
        assert expiration_datetime.date() == kwargs['expiration_datetime'].date()

# Test with one user who has a membership expiring tomorrow and one in a week
def test_individual_expiring_access_notifications_week(db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime1 = datetime.now() + timedelta(days=1)
    expiration_datetime2 = datetime.now() + timedelta(weeks=1)

    ModifyGroupUsers(
        group=okta_group,
        users_added_ended_at=expiration_datetime1,
        members_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        users_added_ended_at=expiration_datetime2,
        members_to_add=[user.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_user"
    )

    expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 2
    assert okta_group in kwargs['groups']
    assert role_group in kwargs['groups']
    assert user == kwargs['user']
    assert kwargs['expiration_datetime'] is None


# Test with one user who has one direct membership expiring tomorrow and a role membership for the same group
def test_individual_expiring_direct_with_role(db: SQLAlchemy, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)
    other_date = datetime.now() + timedelta(days=90)

    ModifyGroupUsers(
        group=okta_group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=role_group,
        users_added_ended_at=other_date,
        members_to_add=[user.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=role_group,
        groups_added_ended_at=other_date,
        groups_to_add=[okta_group.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_user"
    )

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
        group=group1,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user1.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user2.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group1,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 2
    assert group1 in kwargs['groups']
    assert group2 in kwargs['groups']
    assert len(kwargs['users']) == 2
    assert user1 in kwargs['users']
    assert user2 in kwargs['users']
    assert kwargs['roles'] is None

# Test with one owner who owns one group, the owner is a member of the group and their access is expiring this week
def test_owner_expiring_access_notifications_owner_only_member(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

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
        group=group1,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user1.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group1,
        users_added_ended_at=expiration_datetime,
        members_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user2.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group1,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 2
    assert group1 in kwargs['groups']
    assert group2 in kwargs['groups']
    assert len(kwargs['users']) == 2
    assert user1 in kwargs['users']
    assert user2 in kwargs['users']
    assert kwargs['roles'] is None


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
        group=group1,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user1.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user2.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group1,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 2
    assert group1 in kwargs['groups']
    assert group2 in kwargs['groups']
    assert len(kwargs['users']) == 2
    assert user1 in kwargs['users']
    assert user2 in kwargs['users']
    assert kwargs['roles'] is None


# Test with one owner who owns one group, the owner is a member of the group and their access is expiring next week
def test_owner_expiring_access_notifications_owner_only_member_week(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(group)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group,
        users_added_ended_at=expiration_datetime,
        members_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

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
        group=group1,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user1.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group1,
        users_added_ended_at=expiration_datetime,
        members_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        users_added_ended_at=expiration_datetime,
        members_to_add=[user2.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group1,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 2
    assert group1 in kwargs['groups']
    assert group2 in kwargs['groups']
    assert len(kwargs['users']) == 2
    assert user1 in kwargs['users']
    assert user2 in kwargs['users']
    assert kwargs['roles'] is None


# Test with one owner who owns two groups, each group has a role member whose access expires this week
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

    ModifyGroupUsers(
        group=group1,
        users_added_ended_at=None,
        members_to_add=[user1.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=group1,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[group2.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 1
    assert group2 in kwargs['groups']
    assert kwargs['users'] is None
    assert len(kwargs['roles']) == 1
    assert group1 in kwargs['roles']

# Test with one owner who owns two groups, each group has a role member whose access expires next week
def test_owner_expiring_access_notifications_role_week(db: SQLAlchemy, mocker: MockerFixture) -> None:
    group1 = RoleGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(owner)
    db.session.commit()

    ModifyGroupUsers(
        group=group1,
        users_added_ended_at=None,
        members_to_add=[user1.id],
        sync_to_okta=False
    ).execute()
    ModifyGroupUsers(
        group=group2,
        owners_to_add=[owner.id],
        sync_to_okta=False
    ).execute()
    ModifyRoleGroups(
        role_group=group1,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[group2.id],
        sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(
        hook, "access_expiring_owner"
    )

    expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs['groups']) == 1
    assert group2 in kwargs['groups']
    assert kwargs['users'] is None
    assert len(kwargs['roles']) == 1
    assert group1 in kwargs['roles']