from collections import namedtuple
from datetime import datetime, timedelta
from typing import Callable, Tuple

from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from api.models import AppGroup, OktaGroup, OktaUser, OktaUserGroupMember
from api.services import okta
from api.services.okta_service import Group, User
from api.syncer import sync_group_ownerships
from tests.factories import AppFactory, AppGroupFactory, GroupFactory, UserFactory

OwnershipDetails = namedtuple("OwnershipDetails", ["expired_at", "db_pk"])


def test_ownership_sync(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.build_batch(3)
    initial_db_users, initial_db_groups = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    ownerships = run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, False)

    assert ownerships is not None


def test_ownership_in_okta_not_in_db_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    delete_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")

    _ = run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, True)

    owners = _get_group_owners(db, initial_okta_groups[0].id)

    assert delete_ownership_spy.call_count == 3

    assert len(owners) == 0


def test_ownership_in_okta_not_in_db_not_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    _ = run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, False)

    owners = _get_group_owners(db, initial_okta_groups[0].id)

    assert owners is not None
    assert len(owners) == 3


def test_ownership_in_db_not_in_okta_not_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group ownership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired owner
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, False)

    owners_rows = _get_group_owners(db, initial_okta_groups[0].id)

    assert remove_ownership_spy.call_count == 0
    assert len(owners_rows) == 2
    # Verify expired user is not updated
    assert owners_rows[initial_okta_users[0].id].expired_at == expired_date
    assert owners_rows[initial_okta_users[1].id].expired_at < non_expired_date


def test_ownership_in_db_not_in_okta_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group ownership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired owner
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, True)

    owners_rows = _get_group_owners(db, initial_okta_groups[0].id)

    assert add_ownership_spy.call_count == 1
    assert len(owners_rows) == 2
    # Verify expired user is not updated
    assert owners_rows[initial_okta_users[0].id].expired_at == expired_date
    assert owners_rows[initial_okta_users[1].id].expired_at == non_expired_date


def test_app_ownership_in_db_not_in_okta_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    app = AppFactory.build()
    app_owner_group = AppGroupFactory.build(
        app_id=app.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Owners",
    )
    app_non_owner_group = AppGroupFactory.build(
        app_id=app.id,
        is_owner=False,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}NoneOwners",
    )
    db.session.add(app)
    db.session.add(app_owner_group)
    db.session.add(app_non_owner_group)
    db.session.commit()

    _, _ = seed_db(db, initial_okta_users, [])

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group ownership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        app_non_owner_group.id,
        expired_date,
    )

    # Add non-expired owner
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[1].id,
        app_owner_group.id,
        non_expired_date,
    )

    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")

    run_sync(
        db,
        mocker,
        [
            GroupFactory.create(
                config={
                    "name": app_owner_group.name,
                    "id": app_owner_group.id,
                    "type": "OKTA_GROUP",
                }
            ),
            GroupFactory.create(
                config={
                    "name": app_non_owner_group.name,
                    "id": app_non_owner_group.id,
                    "type": "OKTA_GROUP",
                }
            ),
        ],
        fake_list_owners_for_group,
        True,
    )

    owners_rows = {}
    owners_rows.update(_get_group_owners(db, app_owner_group.id))
    owners_rows.update(_get_group_owners(db, app_non_owner_group.id))

    # Expect adding one owner to the app owner Okta group and one owner to the app non-owner Okta group
    assert add_ownership_spy.call_count == 2
    assert len(owners_rows) == 2
    # Verify expired user is not updated
    assert owners_rows[initial_okta_users[0].id].expired_at == expired_date
    assert owners_rows[initial_okta_users[1].id].expired_at == non_expired_date


def test_ownership_in_both_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0], initial_okta_users[1]]
        return []

    # Add expired group ownership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired owner
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, True)

    owners_rows = _get_group_owners(db, initial_okta_groups[0].id)

    assert remove_ownership_spy.call_count == 1
    assert len(owners_rows) == 2
    # Verify expired user is not updated
    assert owners_rows[initial_okta_users[0].id].expired_at == expired_date
    # Verify valid user is untouched
    assert owners_rows[initial_okta_users[1].id].expired_at == non_expired_date

    return


def test_ownership_in_both_non_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0], initial_okta_users[1]]
        return []

    # Add expired group ownership
    expired_date = datetime.now() - timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired owner
    non_expired_date = datetime.now() + timedelta(days=1)
    _add_group_ownership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, False)

    owners_rows = _get_group_owners(db, initial_okta_groups[0].id)

    assert remove_ownership_spy.call_count == 0
    assert len(owners_rows) == 2

    # Verify expired user is synced in. 'None' expiry date indicates
    # ownership was synced in from okta
    assert owners_rows[initial_okta_users[0].id].expired_at is None

    # Verify that non-expired ownerships are unchanged
    assert owners_rows[initial_okta_users[1].id].expired_at == non_expired_date

    return


def test_ownership_unamanaged_group_in_okta_not_in_db_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    delete_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")

    _ = run_sync(
        db,
        mocker,
        initial_okta_groups,
        fake_list_owners_for_group,
        True,
        groups_with_rules={initial_okta_groups[0].id},
    )

    owners = _get_group_owners(db, initial_okta_groups[0].id)

    # Verify no deletes occurred because the group was listed as managed
    assert delete_ownership_spy.call_count == 0
    assert len(owners) == 3


def test_ownership_through_multiple_groups_non_authoritative(db: SQLAlchemy, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_owners_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0]]
        return []

    # Add group ownership for user 0
    date_1 = datetime.now() + timedelta(days=2)
    pk_1 = _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        date_1,
    )

    # Add another ownership for user 0
    date_2 = datetime.now() + timedelta(days=1)
    pk_2 = _add_group_ownership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        date_2,
    )

    remove_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")
    run_sync(db, mocker, initial_okta_groups, fake_list_owners_for_group, False)

    assert remove_ownership_spy.call_count == 0

    # Verify expired user is synced in. 'None' expiry date indicates
    # ownership was synced in from okta
    assert _get_group_ownership(db, pk_1).expired_at == date_1
    assert _get_group_ownership(db, pk_2).expired_at == date_2


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
    user_ownership_func: Callable[[str], list[User]],
    act_as_authority: bool,
    groups_with_rules: set[str] = set(),
) -> list[OktaGroup]:
    with Session(db.engine) as session:
        # Initialize Okta service with group owners API enabled
        okta.initialize("test.okta.com", "test-token", use_group_owners_api=True)

        mocker.patch.object(okta, "list_groups", return_value=okta_groups)

        mocker.patch.object(okta, "list_owners_for_group", side_effect=user_ownership_func)

        mocker.patch.object(okta, "list_groups_with_active_rules", return_value=groups_with_rules)

        sync_group_ownerships(act_as_authority)

        return session.query(OktaUserGroupMember).all()


def _get_group_owners(db: SQLAlchemy, group_id: str) -> dict[str, OwnershipDetails]:
    return {
        m.user_id: OwnershipDetails(m.ended_at, m.id)
        for m in (
            db.session.query(OktaUserGroupMember)
            .filter(OktaUserGroupMember.group_id == group_id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .all()
        )
    }


def _get_group_ownership(db: SQLAlchemy, ownership_id: int) -> OwnershipDetails:
    ownership = (
        db.session.query(OktaUserGroupMember)
        .filter(OktaUserGroupMember.id == ownership_id)
        .filter(OktaUserGroupMember.is_owner.is_(True))
        .one()
    )

    return OwnershipDetails(ownership.ended_at, ownership.id)


def _add_group_ownership_record(db: SQLAlchemy, user_id: str, group_id: str, ended_at: datetime) -> int:
    ownership = OktaUserGroupMember(user_id=user_id, group_id=group_id, ended_at=ended_at, is_owner=True)
    db.session.add(ownership)
    db.session.commit()

    db.session.refresh(ownership)
    return ownership.id
