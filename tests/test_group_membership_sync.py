from collections import namedtuple
from datetime import datetime, timedelta
from typing import Callable, Tuple

from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import OktaGroup, OktaUser, OktaUserGroupMember
from api.extensions import Db
from api.services import okta
from api.services.okta_service import Group, User
from api.syncer import sync_group_memberships
from tests.factories import GroupFactory, UserFactory

MembershipDetails = namedtuple("MembershipDetails", ["expired_at", "db_pk"])


async def test_membership_sync(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    memberships = await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    assert memberships is not None


async def test_membership_in_okta_not_in_db_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    delete_membership_spy = mocker.patch.object(okta, "remove_user_from_group")

    _ = await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, True)

    members = await _get_group_members(db, initial_okta_groups[0].id)

    assert delete_membership_spy.call_count == 3

    assert len(members) == 0


async def test_membership_in_okta_not_in_db_not_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    _ = await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    members = await _get_group_members(db, initial_okta_groups[0].id)

    assert members is not None
    assert len(members) == 3


async def test_membership_in_db_not_in_okta_not_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    members_rows = await _get_group_members(db, initial_okta_groups[0].id)

    # The spy used to target only the syncer's direct (authoritative) removal
    # path; ModifyGroupUsers' internal removal went through the separate
    # `async_remove_user_from_group` name. With the names merged, the single
    # ModifyGroupUsers removal of the non-expired member now hits the spy too.
    assert remove_membership_spy.call_count == 1
    assert len(members_rows) == 2
    # Verify expired user is not updated
    assert members_rows[initial_okta_users[0].id].expired_at == expired_date
    assert members_rows[initial_okta_users[1].id].expired_at < non_expired_date


async def test_membership_in_db_not_in_okta_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")
    await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, True)

    members_rows = await _get_group_members(db, initial_okta_groups[0].id)

    assert add_membership_spy.call_count == 1
    assert len(members_rows) == 2
    # Verify expired user is not updated
    assert members_rows[initial_okta_users[0].id].expired_at == expired_date
    assert members_rows[initial_okta_users[1].id].expired_at == non_expired_date


async def test_membership_in_both_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0], initial_okta_users[1]]
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, True)

    members_rows = await _get_group_members(db, initial_okta_groups[0].id)

    assert remove_membership_spy.call_count == 1
    assert len(members_rows) == 2
    # Verify expired user is not updated
    assert members_rows[initial_okta_users[0].id].expired_at == expired_date
    # Verify valid user is untouched
    assert members_rows[initial_okta_users[1].id].expired_at == non_expired_date

    return


async def test_membership_in_both_non_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0], initial_okta_users[1]]
        return []

    # Add expired group membership
    expired_date = datetime.now() - timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        expired_date,
    )

    # Add non-expired member
    non_expired_date = datetime.now() + timedelta(days=1)
    await _add_group_membership_record(
        db,
        initial_okta_users[1].id,
        initial_okta_groups[0].id,
        non_expired_date,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    members_rows = await _get_group_members(db, initial_okta_groups[0].id)

    assert remove_membership_spy.call_count == 0
    assert len(members_rows) == 2

    # Verify expired user is synced in. 'None' expiry date indicates
    # membership was synced in from okta
    assert members_rows[initial_okta_users[0].id].expired_at is None

    # Verify that non-expired memberships are unchanged
    assert members_rows[initial_okta_users[1].id].expired_at == non_expired_date

    return


async def test_membership_unamanaged_group_in_okta_not_in_db_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    initial_db_users, initial_db_groups = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return initial_okta_users
        return []

    delete_membership_spy = mocker.patch.object(okta, "remove_user_from_group")

    _ = await run_sync(
        db,
        mocker,
        initial_okta_groups,
        fake_list_users_for_group,
        True,
        groups_with_rules={initial_okta_groups[0].id},
    )

    members = await _get_group_members(db, initial_okta_groups[0].id)

    # Verify no deletes occurred because the group was listed as managed
    assert delete_membership_spy.call_count == 0
    assert len(members) == 3


async def test_membership_through_multiple_groups_non_authoritative(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            return [initial_okta_users[0]]
        return []

    # Add group membership for user 0
    date_1 = datetime.now() + timedelta(days=2)
    pk_1 = await _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        date_1,
    )

    # Add another membership for user 0
    date_2 = datetime.now() + timedelta(days=1)
    pk_2 = await _add_group_membership_record(
        db,
        initial_okta_users[0].id,
        initial_okta_groups[0].id,
        date_2,
    )

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    assert remove_membership_spy.call_count == 0

    # Verify expired user is synced in. 'None' expiry date indicates
    # membership was synced in from okta
    assert (await _get_group_membership(db, pk_1)).expired_at == date_1
    assert (await _get_group_membership(db, pk_2)).expired_at == date_2


async def test_membership_sync_continues_after_group_failure(db: Db, mocker: MockerFixture) -> None:
    initial_okta_users = UserFactory.create_batch(3)
    initial_okta_groups = GroupFactory.create_batch(3)
    _, _ = await seed_db(db, initial_okta_users, initial_okta_groups)

    def fake_list_users_for_group(group_id: str) -> list[User]:
        if group_id == initial_okta_groups[0].id:
            raise Exception("Simulated Okta timeout")
        if group_id == initial_okta_groups[1].id:
            return initial_okta_users
        return []

    _ = await run_sync(db, mocker, initial_okta_groups, fake_list_users_for_group, False)

    # Verify the second group still got its members synced despite the first group failing
    members = await _get_group_members(db, initial_okta_groups[1].id)
    assert len(members) == 3

    # Verify the failing group has no members
    failed_members = await _get_group_members(db, initial_okta_groups[0].id)
    assert len(failed_members) == 0


async def seed_db(db: Db, users: list[OktaUser], groups: list[OktaGroup]) -> Tuple[list[OktaUser], list[OktaGroup]]:
    async with AsyncSession(db.engine) as session:
        session.add_all([Group(g).update_okta_group(OktaGroup(), {}) for g in groups])
        session.add_all([User(u).update_okta_user(OktaUser(), {}) for u in users])
        await session.commit()

        return (
            list((await session.scalars(select(OktaUser))).all()),
            list((await session.scalars(select(OktaGroup))).all()),
        )


async def run_sync(
    db: Db,
    mocker: MockerFixture,
    okta_groups: list[OktaGroup],
    user_membership_func: Callable[[str], list[User]],
    act_as_authority: bool,
    groups_with_rules: set[str] = set(),
) -> list[OktaUserGroupMember]:
    async with AsyncSession(db.engine) as session:
        mocker.patch.object(okta, "list_groups", return_value=okta_groups)

        mocker.patch.object(okta, "list_users_for_group", side_effect=user_membership_func)

        mocker.patch.object(okta, "list_groups_with_active_rules", return_value=groups_with_rules)

        await sync_group_memberships(act_as_authority, concurrency=10)

        return list((await session.scalars(select(OktaUserGroupMember))).all())


async def _get_group_members(db: Db, group_id: str) -> dict[str, MembershipDetails]:
    return {
        m.user_id: MembershipDetails(m.ended_at, m.id)
        for m in (
            await db.session.scalars(
                select(OktaUserGroupMember)
                .where(OktaUserGroupMember.group_id == group_id)
                .where(OktaUserGroupMember.is_owner.is_(False))
                # Keyed by user_id below, so the newest row per user must win
                # regardless of which scan plan (and row order) the DB picks.
                .order_by(OktaUserGroupMember.id)
            )
        ).all()
    }


async def _get_group_membership(db: Db, membership_id: int) -> MembershipDetails:
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.id == membership_id)
            .where(OktaUserGroupMember.is_owner.is_(False))
        )
    ).one()

    return MembershipDetails(membership.ended_at, membership.id)


async def _add_group_membership_record(db: Db, user_id: str, group_id: str, ended_at: datetime) -> int:
    membership = OktaUserGroupMember(user_id=user_id, group_id=group_id, ended_at=ended_at, is_owner=False)
    db.session.add(membership)
    await db.session.commit()

    await db.session.refresh(membership)
    return membership.id
