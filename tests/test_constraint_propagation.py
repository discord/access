from datetime import UTC, datetime, timedelta

from pytest_mock import MockerFixture
from sqlalchemy import select

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.operations import ModifyGroupUsers
from api.services import okta
from tests.factories import (
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    OktaUserFactory,
    RoleGroupFactory,
    TagFactory,
)


async def _role_associated_with_tagged_group(
    db: Db, *, constraints: dict, is_owner: bool, propagate: bool = True
) -> tuple[RoleGroup, OktaGroup]:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints=constraints, propagate_to_roles=propagate)
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=is_owner))
    await db.session.commit()
    return role, group


async def test_owner_association_blocks_self_add_to_role(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    """A role that OWNS a group with disallow_self_add_ownership: the role's
    owner cannot add themself as a member of the role. This is the case the
    deleted owner loop used to cover."""
    mocker.patch.object(okta, "add_user_to_group")
    role, _ = await _role_associated_with_tagged_group(
        db, constraints={Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True}, is_owner=True
    )
    db.session.add(user)
    await db.session.commit()

    await ModifyGroupUsers(group=role, members_to_add=[user.id], current_user_id=user.id, sync_to_okta=False).execute()

    refreshed = await db.session.get(RoleGroup, role.id)
    assert refreshed is not None
    # The self-add was rejected, so no membership was created.
    memberships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).all()
    assert len(memberships) == 0


async def test_owner_association_requires_reason_to_add_role_member(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """A role that OWNS a group tagged require_owner_reason: True, with no
    member association to any tagged group. Adding a member to the role
    without a reason must be rejected; with a reason it must succeed. This
    isolates the owner-association branch of CheckForReason.execute_for_group
    -- the only other test touching this path (test_require_reason_modify_group_users)
    is confounded because its role is simultaneously a member of a
    require_member_reason group and an owner of a require_owner_reason group,
    so either source alone would make it pass even if this branch regressed."""
    mocker.patch.object(okta, "add_user_to_group")
    role, _ = await _role_associated_with_tagged_group(
        db, constraints={Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True}, is_owner=True
    )
    db.session.add(user)
    await db.session.commit()

    # Without a reason, the add is rejected.
    await ModifyGroupUsers(group=role, members_to_add=[user.id], current_user_id=user.id, sync_to_okta=False).execute()

    memberships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).all()
    assert len(memberships) == 0

    # With a reason, the same add succeeds -- proving the rejection above came
    # from the reason constraint, not from some other cause.
    await ModifyGroupUsers(
        group=role,
        members_to_add=[user.id],
        current_user_id=user.id,
        sync_to_okta=False,
        created_reason="need this for on-call rotation",
    ).execute()

    memberships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).all()
    assert len(memberships) == 1


async def test_member_association_blocks_self_add_to_role(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    """Parity with today: a role that is a MEMBER of a group with
    disallow_self_add_membership still blocks."""
    mocker.patch.object(okta, "add_user_to_group")
    role, _ = await _role_associated_with_tagged_group(
        db, constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True}, is_owner=False
    )
    db.session.add(user)
    await db.session.commit()

    await ModifyGroupUsers(group=role, members_to_add=[user.id], current_user_id=user.id, sync_to_okta=False).execute()

    memberships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).all()
    assert len(memberships) == 0


async def test_gate_off_allows_self_add_to_role(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    mocker.patch.object(okta, "add_user_to_group")
    role, _ = await _role_associated_with_tagged_group(
        db,
        constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True},
        is_owner=False,
        propagate=False,
    )
    db.session.add(user)
    await db.session.commit()

    await ModifyGroupUsers(group=role, members_to_add=[user.id], current_user_id=user.id, sync_to_okta=False).execute()

    memberships = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).all()
    assert len(memberships) == 1


async def test_member_time_limit_reaches_role_members(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    mocker.patch.object(okta, "add_user_to_group")
    role, _ = await _role_associated_with_tagged_group(
        db, constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, is_owner=False
    )
    actor = OktaUserFactory.build()
    db.session.add_all([user, actor])
    await db.session.commit()

    await ModifyGroupUsers(group=role, members_to_add=[user.id], current_user_id=actor.id, sync_to_okta=False).execute()

    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).one()
    assert membership.ended_at is not None
    expected = datetime.now(UTC) + timedelta(seconds=86400)
    assert abs((membership.ended_at.replace(tzinfo=UTC) - expected).total_seconds()) < 60


async def test_owner_time_limit_reaches_members_of_an_owning_role(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """The new control: a role that OWNS a group is capped by that group's
    OWNER time limit."""
    mocker.patch.object(okta, "add_user_to_group")
    role, _ = await _role_associated_with_tagged_group(
        db, constraints={Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 3600}, is_owner=True
    )
    actor = OktaUserFactory.build()
    db.session.add_all([user, actor])
    await db.session.commit()

    await ModifyGroupUsers(group=role, members_to_add=[user.id], current_user_id=actor.id, sync_to_okta=False).execute()

    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).one()
    assert membership.ended_at is not None
    expected = datetime.now(UTC) + timedelta(seconds=3600)
    assert abs((membership.ended_at.replace(tzinfo=UTC) - expected).total_seconds()) < 60
