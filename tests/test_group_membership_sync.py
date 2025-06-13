from collections import namedtuple
from datetime import datetime, timedelta
from typing import Callable, Tuple

from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from api.models import OktaGroup, OktaUser, OktaUserGroupMember
from api.services import okta
from api.services.okta_service import Group, User
from api.syncer import sync_group_memberships
from tests.factories import GroupFactory, UserFactory

MembershipDetails = namedtuple("MembershipDetails", ["expired_at", "db_pk"])


def test_membership_sync(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    memberships = run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    assert memberships is not None


def test_membership_in_okta_not_in_db_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    delete_membership_spy = mocker.patch.object(okta, "remove_user_from_group")

    _ = run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, True)

    members = _get_group_members(db, initial_okta_groups[0].id)

    assert delete_membership_spy.call_count == 3

    assert len(members) == 0


def test_membership_in_okta_not_in_db_not_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    _ = run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    members = _get_group_members(db, initial_okta_groups[0].id)

    assert members is not None
    assert len(members) == 3


def test_membership_in_db_not_in_okta_not_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    members_rows = _get_group_members(db, initial_okta_groups[0].id)

    assert remove_membership_spy.call_count == 0
    assert len(members_rows) == 2
    # Verify expired user is not updated
    assert members_rows[initial_okta_users[0].id].expired_at == expired_date
    assert members_rows[initial_okta_users[1].id].expired_at < non_expired_date


def test_membership_in_db_not_in_okta_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, True)

    members_rows = _get_group_members(db, initial_okta_groups[0].id)

    assert add_membership_spy.call_count == 1
    assert len(members_rows) == 2
    # Verify expired user is not updated
    assert members_rows[initial_okta_users[0].id].expired_at == expired_date
    assert members_rows[initial_okta_users[1].id].expired_at == non_expired_date


def test_membership_in_both_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0], initial_okta_users[1]]
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, True)

    members_rows = _get_group_members(db, initial_okta_groups[0].id)

    assert remove_membership_spy.call_count == 1
    assert len(members_rows) == 2
    # Verify expired user is not updated
    assert members_rows[initial_okta_users[0].id].expired_at == expired_date
    # Verify valid user is untouched
    assert members_rows[initial_okta_users[1].id].expired_at == non_expired_date

    return


def test_membership_in_both_non_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0], initial_okta_users[1]]
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    members_rows = _get_group_members(db, initial_okta_groups[0].id)

    assert remove_membership_spy.call_count == 0
    assert len(members_rows) == 2

    # Verify expired user is synced in. 'None' expiry date indicates
    # membership was synced in from okta
    assert members_rows[initial_okta_users[0].id].expired_at is None

    # Verify that non-expired memberships are unchanged
    assert members_rows[initial_okta_users[1].id].expired_at == non_expired_date

    return


def test_membership_unamanaged_group_in_okta_not_in_db_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    delete_membership_spy = mocker.patch.object(okta, "remove_user_from_group")

    _ = run_sync(
        db,
        mocker,
        initial_okta_groups,
        fake_list_users_for_group,
        True,
        groups_with_rules={initial_okta_groups[0].id},
    )

    members = _get_group_members(db, initial_okta_groups[0].id)

    # Verify no deletes occurred because the group was listed as managed
    assert delete_membership_spy.call_count == 0
    assert len(members) == 3


def test_membership_through_multiple_groups_non_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0]]
        return []

    # Add group membership for user 0
    date_1 = datetime.now() + timedelta(days=2)
    pk_1 = _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        date_1,
    )

    # Add another membership for user 0
    date_2 = datetime.now() + timedelta(days=1)
    pk_2 = _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        date_2,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    assert remove_membership_spy.call_count == 0

    # Verify expired user is synced in. 'None' expiry date indicates
    # membership was synced in from okta
    assert _get_group_membership(db, pk_1).expired_at == date_1
    assert _get_group_membership(db, pk_2).expired_at == date_2


def seed_db(db: SQLAlchemy, users: list[OktaUser], groups: list[OktaGroup]) -> Tuple[list[OktaUser], list[OktaGroup]]:
    with Session(db.engine) as session:
        session.add_all([Group(g).update_okta_group(OktaGroup(), {}) for g in groups])
        session.add_all([User(u).update_okta_user(OktaUser(), {}) for u in users])
        session.commit()

        return (session.query(OktaUser).all(), session.query(OktaGroup).all())


def run_sync(
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_groups: list[OktaGroup],
    user_membership_func: Callable[[str], list[User]],
    act_as_authority: bool,
    groups_with_rules: set[str] = set(),
) -> list[OktaGroup]:
    with Session(db.engine) as session:
        # Initialize Okta service
        okta.initialize("test.okta.com", "test-token")

        mocker.patch.object(okta, "list_groups", return_value=okta_groups)
        mocker.patch.object(okta, "list_users_for_group", side_effect=user_membership_func)
        mocker.patch.object(okta, "list_groups_with_active_rules", return_value=groups_with_rules)
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        sync_group_memberships(act_as_authority)

        return session.query(OktaUserGroupMember).all()


def _get_group_members(db: SQLAlchemy, group_id: str) -> dict[str, MembershipDetails]:
    return {
        m.user_id: MembershipDetails(m.ended_at, m.id)
        for m in (
            db.session.query(OktaUserGroupMember)
            .filter(OktaUserGroupMember.group_id == group_id)
            .filter(OktaUserGroupMember.is_owner.is_(False))
            .all()
        )
    }


def _get_group_membership(db: SQLAlchemy, membership_id: int) -> MembershipDetails:
    membership = (
        db.session.query(OktaUserGroupMember)
        .filter(OktaUserGroupMember.id == membership_id)
        .filter(OktaUserGroupMember.is_owner.is_(False))
        .one()
    )

    return MembershipDetails(membership.ended_at, membership.id)


def _add_group_membership_record(db: SQLAlchemy, user_id: str, group_id: str, ended_at: datetime) -> int:
    membership = OktaUserGroupMember(user_id=user_id, group_id=group_id, ended_at=ended_at, is_owner=False)
    db.session.add(membership)
    db.session.commit()

    db.session.refresh(membership)
    return membership.id
