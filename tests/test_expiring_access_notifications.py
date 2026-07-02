import threading
from datetime import datetime, timedelta
from typing import Any

from pytest_mock import MockerFixture
from sqlalchemy import select

from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.extensions import Db
from api.config import settings
from api.operations import ModifyGroupUsers, ModifyRoleGroups
from api.plugins import get_notification_hook
from fastapi import FastAPI

from api.syncer import (
    expiring_access_notifications_owner,
    expiring_access_notifications_role_owner,
    expiring_access_notifications_user,
)
from tests.factories import OktaGroupFactory, OktaUserFactory, RoleGroupFactory


# Test with one user who has two memberships expiring tomorrow
async def test_individual_expiring_access_notifications(
    db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    await ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    membership1 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.group_id == okta_group.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.group_id == role_group.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    await expiring_access_notifications_user()

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
async def test_individual_expiring_access_notifications_week(
    db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()

    expiration_datetime1 = datetime.now() + timedelta(days=1)
    expiration_datetime2 = datetime.now() + timedelta(weeks=1)

    await ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime1, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime2, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    membership1 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.group_id == okta_group.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.group_id == role_group.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    await expiring_access_notifications_user()

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
async def test_individual_expiring_direct_with_role(
    db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)
    other_date = datetime.now() + timedelta(days=90)

    await ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=other_date, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role_group, groups_added_ended_at=other_date, groups_to_add=[okta_group.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    await expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 0


# Test with one user who has one direct membership expiring in a week and a role membership for the same group
async def test_individual_expiring_direct_with_role_week(
    db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()

    expiration_datetime = datetime.now() + timedelta(weeks=1)
    other_date = datetime.now() + timedelta(days=90)

    await ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=other_date, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role_group, groups_added_ended_at=other_date, groups_to_add=[okta_group.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    await expiring_access_notifications_user()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two groups, each group has a member whose access expires this week
async def test_owner_expiring_access_notifications(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group1.id)
            .where(OktaUserGroupMember.user_id == user1.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group2.id)
            .where(OktaUserGroupMember.user_id == user2.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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
async def test_owner_expiring_access_notifications_owner_only_member(db: Db, mocker: MockerFixture) -> None:
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group)
    db.session.add(owner)
    await db.session.commit()

    await ModifyGroupUsers(
        group=group, users_added_ended_at=expiration_datetime, members_to_add=[owner.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group, owners_to_add=[owner.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two groups, each group has a member whose access expires this week
# The owner is also a group member with expiring access but their own access should not be included.
async def test_owner_expiring_access_notifications_owner_member(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id, owner.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group1.id)
            .where(OktaUserGroupMember.user_id == user1.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group1.id)
            .where(OktaUserGroupMember.user_id == owner.id)
        )
    ).first()
    membership3 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group2.id)
            .where(OktaUserGroupMember.user_id == user2.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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
async def test_owner_expiring_access_notifications_week(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group1.id)
            .where(OktaUserGroupMember.user_id == user1.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group2.id)
            .where(OktaUserGroupMember.user_id == user2.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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
async def test_owner_expiring_access_notifications_owner_only_member_week(db: Db, mocker: MockerFixture) -> None:
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=9)

    db.session.add(group)
    db.session.add(owner)
    await db.session.commit()

    await ModifyGroupUsers(
        group=group, users_added_ended_at=expiration_datetime, members_to_add=[owner.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group, owners_to_add=[owner.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two groups, each group has a member whose access expires next week
# The owner is also a group member with expiring access but their own access should not be included.
async def test_owner_expiring_access_notifications_owner_member_week(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user1.id, owner.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user2.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()

    membership1 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group1.id)
            .where(OktaUserGroupMember.user_id == user1.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group1.id)
            .where(OktaUserGroupMember.user_id == owner.id)
        )
    ).first()
    membership3 = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group2.id)
            .where(OktaUserGroupMember.user_id == user2.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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
async def test_owner_expiring_access_notifications_role(db: Db, mocker: MockerFixture) -> None:
    group1 = RoleGroupFactory.create()
    group2 = OktaGroupFactory.create()
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user1)
    db.session.add(owner)
    await db.session.commit()

    await ModifyGroupUsers(
        group=group1, users_added_ended_at=None, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(
        role_group=group1, groups_added_ended_at=expiration_datetime, groups_to_add=[group2.id], sync_to_okta=False
    ).execute()

    membership1 = (
        await db.session.scalars(
            select(RoleGroupMap)
            .where(RoleGroupMap.group_id == group2.id)
            .where(RoleGroupMap.role_group_id == group1.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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
async def test_owner_expiring_access_notifications_role_week(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(
        group=role, users_added_ended_at=None, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=group1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=group2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(
        role_group=role,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[group1.id, group2.id],
        sync_to_okta=False,
    ).execute()

    membership1 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.group_id == group1.id).where(RoleGroupMap.role_group_id == role.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.group_id == group2.id).where(RoleGroupMap.role_group_id == role.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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
async def test_individual_do_not_renew_notification_behavior(
    db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.add(user)
    await db.session.commit()

    expiration_datetime = datetime.now() + timedelta(days=1)

    # Add user to two groups
    await ModifyGroupUsers(
        group=okta_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    # Get the OktaUserGroupMember for the user's membership to role_group
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user.id)
            .where(OktaUserGroupMember.group_id == role_group.id)
        )
    ).first()
    assert membership is not None

    # Mark one membership as 'should_expire'
    await ModifyGroupUsers(group=role_group, members_should_expire=[membership.id], sync_to_okta=False).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_user")

    await expiring_access_notifications_user()

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
async def test_owner_role_do_not_renew_notification_behavior(
    db: Db, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    user1 = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(user1)
    db.session.add(owner)
    await db.session.commit()

    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=expiration_datetime, members_to_add=[user1.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=role_group, users_added_ended_at=None, owners_to_add=[owner.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(group=okta_group, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(
        role_group=role_group,
        groups_added_ended_at=expiration_datetime,
        groups_to_add=[okta_group.id],
        sync_to_okta=False,
    ).execute()

    # Get the OktaUserGroupMember for the user's membership to role_group
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.user_id == user1.id)
            .where(OktaUserGroupMember.group_id == role_group.id)
        )
    ).first()
    assert membership is not None
    # Get RoleGroupMap
    role_membership = (
        await db.session.scalars(
            select(RoleGroupMap)
            .where(RoleGroupMap.role_group_id == role_group.id)
            .where(RoleGroupMap.group_id == okta_group.id)
        )
    ).first()
    assert role_membership is not None

    # Mark user as do not renew
    await ModifyGroupUsers(group=role_group, members_should_expire=[membership.id], sync_to_okta=False).execute()

    # Mark role as do not renew
    await ModifyRoleGroups(
        role_group=role_group, groups_should_expire=[role_membership.id], sync_to_okta=False
    ).execute()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

    assert expiring_access_notification_spy.call_count == 0


# Test with one owner who owns two role, each role is a member of a group and its access expires tomorrow
async def test_role_owner_expiring_access_notifications_role_tomorrow(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(group=role1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=role2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(
        role_group=role1, groups_added_ended_at=expiration_datetime, groups_to_add=[group.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role2, groups_added_ended_at=expiration_datetime, groups_to_add=[group.id], sync_to_okta=False
    ).execute()

    membership1 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.role_group_id == role1.id).where(RoleGroupMap.group_id == group.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.role_group_id == role2.id).where(RoleGroupMap.group_id == group.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_role_owner")

    await expiring_access_notifications_role_owner()

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
async def test_role_owner_expiring_access_notifications_role_multiple_dates(db: Db, mocker: MockerFixture) -> None:
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
    await db.session.commit()

    await ModifyGroupUsers(group=role1, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyGroupUsers(group=role2, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(
        role_group=role1, groups_added_ended_at=expiration_datetime, groups_to_add=[group1.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role2, groups_added_ended_at=expiration_datetime2, groups_to_add=[group1.id], sync_to_okta=False
    ).execute()
    await ModifyRoleGroups(
        role_group=role2, groups_added_ended_at=expiration_datetime, groups_to_add=[group2.id], sync_to_okta=False
    ).execute()

    membership1 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.role_group_id == role1.id).where(RoleGroupMap.group_id == group1.id)
        )
    ).first()
    membership2 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.role_group_id == role2.id).where(RoleGroupMap.group_id == group1.id)
        )
    ).first()
    membership3 = (
        await db.session.scalars(
            select(RoleGroupMap).where(RoleGroupMap.role_group_id == role2.id).where(RoleGroupMap.group_id == group2.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_role_owner")

    await expiring_access_notifications_role_owner()

    assert expiring_access_notification_spy.call_count == 1
    _, kwargs = expiring_access_notification_spy.call_args
    assert len(kwargs["roles"]) == 3
    assert membership1 in kwargs["roles"]
    assert membership2 in kwargs["roles"]
    assert membership3 in kwargs["roles"]
    assert kwargs["expiration_datetime"] is None


# Test with an externally managed group and a non-externally managed group. The Access admin should only be notified about
# expiring access for the non-externally managed group
async def test_owner_expiring_access_notifications_managed_group_admin(
    db: Db, app: FastAPI, mocker: MockerFixture
) -> None:
    # Create an externally managed group and an Access managed group
    group1 = OktaGroupFactory.create()
    group1.is_managed = False
    group2 = OktaGroupFactory.create()

    user = OktaUserFactory.create()
    access_owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()

    expiration_datetime = datetime.now() + timedelta(days=2)

    db.session.add(group1)
    db.session.add(group2)
    db.session.add(user)
    await db.session.commit()

    await ModifyGroupUsers(
        group=group1, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()
    await ModifyGroupUsers(
        group=group2, users_added_ended_at=expiration_datetime, members_to_add=[user.id], sync_to_okta=False
    ).execute()

    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group2.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).first()

    hook = get_notification_hook()
    expiring_access_notification_spy = mocker.patch.object(hook, "access_expiring_owner")

    await expiring_access_notifications_owner()

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


# The syncer offloads notification hooks (sync plugin code) while holding ORM objects
# bound to the request's AsyncSession. It invokes them via db.session.run_sync so they
# run on the event loop's own greenlet — never a worker thread — which is what keeps
# passing those session-bound objects safe. This guards that contract: the hook must
# run on the loop thread and must be handed usable, eager-loaded ORM objects.
async def test_expiring_user_notification_hook_runs_on_loop_thread_with_usable_orm_objects(
    db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup
) -> None:
    db.session.add(okta_group)
    db.session.add(user)
    await db.session.commit()

    await ModifyGroupUsers(
        group=okta_group,
        users_added_ended_at=datetime.now() + timedelta(days=1),
        members_to_add=[user.id],
        sync_to_okta=False,
    ).execute()

    hook = get_notification_hook()
    observed: dict[str, object] = {}

    def _record(**kwargs: Any) -> None:
        member = kwargs["okta_user_group_members"][0]
        # The syncer eager-loads active_user/active_group, so a hook can read them
        # (and their columns) without emitting SQL.
        observed["email"] = member.active_user.email
        observed["group_name"] = member.active_group.name
        observed["passed_user_email"] = kwargs["user"].email
        # A hook may also read the plain .group relationship (distinct from the
        # eager-loaded .active_group). It is lazy="raise_on_sql", so this only works
        # because active_group put the row in the identity map and the many-to-one
        # resolves from it via use_get without emitting SQL. Guards that eager-load.
        observed["nonactive_group_name"] = member.group.name
        # run_sync executes the hook on the event loop's thread (a greenlet), not an
        # anyio worker thread. If this were reverted to anyio.to_thread.run_sync the
        # hook would run on a worker thread and this id would differ from the test's.
        observed["thread_id"] = threading.get_ident()

    mocker.patch.object(hook, "access_expiring_user", side_effect=_record)

    await expiring_access_notifications_user()

    assert observed["email"] == user.email
    assert observed["group_name"] == okta_group.name
    assert observed["nonactive_group_name"] == okta_group.name
    assert observed["passed_user_email"] == user.email
    assert observed["thread_id"] == threading.get_ident()


# Companion to the user test above, for the owner and role-owner hooks: they receive
# OktaUserGroupMember / RoleGroupMap association rows and a hook may read the plain
# .user/.group/.role_group relationships off them. Those are lazy="raise_on_sql", so
# they only resolve because the syncer's active_* joinedloads seed the identity map
# and the many-to-ones resolve via use_get. These guard that the eager-loads stay
# broad enough that a hook reading the non-active relationships never emits SQL.
async def test_expiring_owner_notification_hook_can_read_association_relationships(
    db: Db, mocker: MockerFixture
) -> None:
    group = OktaGroupFactory.create()
    member = OktaUserFactory.create()
    owner = OktaUserFactory.create()
    db.session.add_all([group, member, owner])
    await db.session.commit()

    await ModifyGroupUsers(
        group=group,
        users_added_ended_at=datetime.now() + timedelta(days=2),
        members_to_add=[member.id],
        sync_to_okta=False,
    ).execute()
    await ModifyGroupUsers(group=group, owners_to_add=[owner.id], sync_to_okta=False).execute()

    observed: dict[str, object] = {}

    def _record(**kwargs: Any) -> None:
        assoc = kwargs["group_user_associations"][0]
        # Non-active .user / .group relationships (not the eager-loaded active_* ones).
        observed["member_email"] = assoc.user.email
        observed["group_name"] = assoc.group.name

    mocker.patch.object(get_notification_hook(), "access_expiring_owner", side_effect=_record)

    await expiring_access_notifications_owner()

    assert observed["member_email"] == member.email
    assert observed["group_name"] == group.name


async def test_expiring_role_owner_notification_hook_can_read_association_relationships(
    db: Db, mocker: MockerFixture
) -> None:
    role = RoleGroupFactory.create()
    group = OktaGroupFactory.create()
    owner = OktaUserFactory.create()
    db.session.add_all([role, group, owner])
    await db.session.commit()

    await ModifyGroupUsers(group=role, owners_to_add=[owner.id], sync_to_okta=False).execute()
    await ModifyRoleGroups(
        role_group=role,
        groups_added_ended_at=datetime.now() + timedelta(days=1),
        groups_to_add=[group.id],
        sync_to_okta=False,
    ).execute()

    observed: dict[str, object] = {}

    def _record(**kwargs: Any) -> None:
        rgm = kwargs["roles"][0]
        # Non-active .role_group / .group relationships on the RoleGroupMap row.
        observed["role_name"] = rgm.role_group.name
        observed["group_name"] = rgm.group.name

    mocker.patch.object(get_notification_hook(), "access_expiring_role_owner", side_effect=_record)

    await expiring_access_notifications_role_owner()

    assert observed["role_name"] == role.name
    assert observed["group_name"] == group.name
