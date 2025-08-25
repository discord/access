from datetime import UTC, datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.integrity import verify_and_fix_role_memberships
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.services import okta


def test_missing_user_from_group_membership(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=role_group.id))
    member_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=False)
    db.session.add(member_role_group_map)
    owner_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=True)
    db.session.add(owner_role_group_map)
    db.session.commit()

    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")
    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")

    verify_and_fix_role_memberships()

    assert add_membership_spy.call_count == 1
    assert add_ownership_spy.call_count == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 1
    )


def test_missing_user_from_group_membership_with_expiring_role_membership(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(
        OktaUserGroupMember(user_id=user.id, group_id=role_group.id, ended_at=datetime.now(UTC) + timedelta(days=2))
    )
    member_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=False)
    db.session.add(member_role_group_map)
    owner_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=True)
    db.session.add(owner_role_group_map)
    db.session.commit()

    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")
    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")

    verify_and_fix_role_memberships()

    assert add_membership_spy.call_count == 1
    assert add_ownership_spy.call_count == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=1)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=3)),
        )
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=1)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=3)),
        )
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )


def test_missing_user_from_group_membership_with_expiring_role_assignment(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(
        OktaUserGroupMember(user_id=user.id, group_id=role_group.id, ended_at=datetime.now(UTC) + timedelta(days=4))
    )
    member_role_group_map = RoleGroupMap(
        role_group_id=role_group.id,
        group_id=okta_group.id,
        is_owner=False,
        ended_at=datetime.now(UTC) + timedelta(days=2),
    )
    db.session.add(member_role_group_map)
    owner_role_group_map = RoleGroupMap(
        role_group_id=role_group.id,
        group_id=okta_group.id,
        is_owner=True,
        ended_at=datetime.now(UTC) + timedelta(days=6),
    )
    db.session.add(owner_role_group_map)
    db.session.commit()

    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")
    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")

    verify_and_fix_role_memberships()

    assert add_membership_spy.call_count == 1
    assert add_ownership_spy.call_count == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=1)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=3)),
        )
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=3)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=5)),
        )
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )


def test_missing_user_from_group_membership_with_both_expiring(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    db.session.add(OktaUserGroupMember(user_id=user.id, group_id=role_group.id))
    member_role_group_map = RoleGroupMap(
        role_group_id=role_group.id,
        group_id=okta_group.id,
        is_owner=False,
        ended_at=datetime.now(UTC) + timedelta(days=2),
    )
    db.session.add(member_role_group_map)
    owner_role_group_map = RoleGroupMap(
        role_group_id=role_group.id,
        group_id=okta_group.id,
        is_owner=True,
        ended_at=datetime.now(UTC) + timedelta(days=4),
    )
    db.session.add(owner_role_group_map)
    db.session.commit()

    add_membership_spy = mocker.patch.object(okta, "add_user_to_group")
    add_ownership_spy = mocker.patch.object(okta, "add_owner_to_group")

    verify_and_fix_role_memberships()

    assert add_membership_spy.call_count == 1
    assert add_ownership_spy.call_count == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=1)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=3)),
        )
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(
            OktaUserGroupMember.ended_at > (datetime.now(UTC) + timedelta(days=3)),
            OktaUserGroupMember.ended_at < (datetime.now(UTC) + timedelta(days=5)),
        )
        .count()
        == 1
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )


def test_extra_user_from_role_membership(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)

    member_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=False)
    db.session.add(member_role_group_map)
    owner_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=True)
    db.session.add(owner_role_group_map)
    db.session.commit()
    db.session.add(
        OktaUserGroupMember(
            user_id=user.id,
            group_id=okta_group.id,
            role_group_map_id=member_role_group_map.id,
        )
    )
    db.session.add(
        OktaUserGroupMember(
            user_id=user.id,
            group_id=okta_group.id,
            role_group_map_id=owner_role_group_map.id,
            is_owner=True,
        )
    )
    db.session.commit()

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    remove_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")

    verify_and_fix_role_memberships()

    assert remove_membership_spy.call_count == 1
    assert remove_ownership_spy.call_count == 1

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == okta_group.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )


def test_extra_user_from_role_membership_with_direct(
    db: SQLAlchemy, mocker: MockerFixture, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)

    member_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=False)
    db.session.add(member_role_group_map)
    owner_role_group_map = RoleGroupMap(role_group_id=role_group.id, group_id=okta_group.id, is_owner=True)
    db.session.add(owner_role_group_map)
    db.session.commit()
    db.session.add(
        OktaUserGroupMember(
            user_id=user.id,
            group_id=okta_group.id,
            role_group_map_id=member_role_group_map.id,
        )
    )
    db.session.add(
        OktaUserGroupMember(
            user_id=user.id,
            group_id=okta_group.id,
            role_group_map_id=owner_role_group_map.id,
            is_owner=True,
        )
    )
    db.session.add(
        OktaUserGroupMember(
            user_id=user.id,
            group_id=okta_group.id,
        )
    )
    db.session.add(
        OktaUserGroupMember(
            user_id=user.id,
            group_id=okta_group.id,
            is_owner=True,
        )
    )
    db.session.commit()

    remove_membership_spy = mocker.patch.object(okta, "remove_user_from_group")
    remove_ownership_spy = mocker.patch.object(okta, "remove_owner_from_group")

    verify_and_fix_role_memberships()

    assert remove_membership_spy.call_count == 0
    assert remove_ownership_spy.call_count == 0

    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == member_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.role_group_map_id == owner_role_group_map.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 0
    )
    assert (
        OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == okta_group.id)
        .filter(OktaUserGroupMember.ended_at.is_(None))
        .count()
        == 2
    )
