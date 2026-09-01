from datetime import UTC, datetime, timedelta

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.operations import ModifyGroupsTimeLimit, ModifyGroupUsers, ModifyRoleGroups
from api.services import okta
from tests.factories import (
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    OktaUserFactory,
    RoleGroupFactory,
    TagFactory,
)


ONE_DAY = 86400


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
    owner cannot add themself as a member of the role. The group's owner-side
    key governs the role's member side, because members of a role that owns a
    group become owners of it."""
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
    """A role that is a MEMBER of a group with disallow_self_add_membership
    blocks its owner from self-adding, reading the same key on the group."""
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


async def test_retroactive_capping_reaches_existing_role_members(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    """A pre-existing, uncapped membership in a role is capped when a
    time-limited tag lands on a group the role is a member of."""
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    actor = OktaUserFactory.build()
    db.session.add_all([group, role, tag, user, actor])
    await db.session.commit()
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()

    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    await ModifyGroupsTimeLimit(groups=[group.id], tags=[tag.id]).execute()

    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).one()
    assert membership.ended_at is not None


async def test_retroactive_capping_reaches_existing_members_of_an_owning_role(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """The owner-association direction: a role that OWNS a group is capped,
    on its own pre-existing user memberships, by that group's OWNER time
    limit when a time-limited tag lands on the group. A member limit is also
    set on the tag (large enough to be a no-op for this scenario, since the
    role is never a *member* of the group) purely so the group carries both
    constraint keys, matching how tags are configured in practice."""
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 999_999,
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 3600,
        }
    )
    actor = OktaUserFactory.build()
    db.session.add_all([group, role, tag, user, actor])
    await db.session.commit()
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=True))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()

    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    await ModifyGroupsTimeLimit(groups=[group.id], tags=[tag.id]).execute()

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


async def test_retroactive_capping_respects_the_gate(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, propagate_to_roles=False)
    db.session.add_all([group, role, tag, user])
    await db.session.commit()
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()

    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    await ModifyGroupsTimeLimit(groups=[group.id], tags=[tag.id]).execute()

    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role.id)
            .where(OktaUserGroupMember.user_id == user.id)
        )
    ).one()
    assert membership.ended_at is None


@pytest.mark.parametrize(
    ("member_limit", "owner_limit"),
    [(3600, 999_999), (999_999, 3600)],
    ids=["member_side_shorter", "owner_side_shorter"],
)
async def test_member_and_owner_associations_to_one_group_coalesce_to_the_minimum(
    db: Db, mocker: MockerFixture, user: OktaUser, member_limit: int, owner_limit: int
) -> None:
    """A role can be both a MEMBER and an OWNER of the same tagged group. The
    member association reads the group's member limit; the owner association
    reads the owner-side counterpart. Both feed the role's member side, so the
    effective limit is the minimum of the two.

    Parametrized so each direction takes a turn being the shorter one -- if
    either association were dropped, exactly one case would regress to the
    other's (deliberately far larger) limit."""
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: member_limit,
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: owner_limit,
        }
    )
    actor = OktaUserFactory.build()
    db.session.add_all([group, role, tag, user, actor])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=True))
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
    expected = datetime.now(UTC) + timedelta(seconds=min(member_limit, owner_limit))
    assert abs((membership.ended_at.replace(tzinfo=UTC) - expected).total_seconds()) < 60


async def test_retroactive_capping_skips_unmanaged_roles(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    """An externally managed role is exempt from constraint enforcement: both
    `effective_ended_at` (grant time) and the `cap-role-memberships` sweep
    return early for one. The retroactive bulk update must agree, or a tag
    landing on the group would cap grants the other two paths never touch."""
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build(is_managed=False)
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag, user])
    await db.session.commit()
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()

    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    role_id, user_id = role.id, user.id
    await ModifyGroupsTimeLimit(groups=[group.id], tags=[tag.id]).execute()

    db.session.expire_all()
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    assert membership.ended_at is None


# --- Capping when the association is created -------------------------------
#
# The retroactive block above covers a time-limited *tag* landing on a group.
# The other way an association starts being governed is the association itself
# being created: attaching a role to a time-limited group must cap the role's
# existing members, not only the `RoleGroupMap`. Bounding the association alone
# would leave membership *of the role* indefinite, so renewing the role's access
# would rebuild every derived grant from memberships nobody re-examined.


async def test_attaching_a_role_caps_its_existing_members(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag, user])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    # Indefinite membership of the role, established before any association
    # to the tagged group exists.
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()
    role_id, user_id = role.id, user.id

    await ModifyRoleGroups(role_group=role_id, groups_to_add=[group.id], sync_to_okta=False).execute()

    db.session.expire_all()
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    assert membership.ended_at is not None
    expected = datetime.now(UTC) + timedelta(seconds=86400)
    assert abs((membership.ended_at.replace(tzinfo=UTC) - expected).total_seconds()) < 60


async def test_attaching_a_role_as_owner_caps_its_existing_members(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """Members of a role that owns a group become owners of it, so the role's
    member side is capped by the group's OWNER limit. Covering only the member
    association would leave this branch of the fix untested."""
    mocker.patch.object(okta, "add_owner_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 999_999,
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 3600,
        }
    )
    db.session.add_all([group, role, tag, user])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()
    role_id, user_id = role.id, user.id

    await ModifyRoleGroups(role_group=role_id, owner_groups_to_add=[group.id], sync_to_okta=False).execute()

    db.session.expire_all()
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    assert membership.ended_at is not None
    expected = datetime.now(UTC) + timedelta(seconds=3600)
    assert abs((membership.ended_at.replace(tzinfo=UTC) - expected).total_seconds()) < 60


async def test_attaching_a_role_respects_the_gate(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    """A tag that does not propagate must not cap the role's members when the
    association is created, exactly as it does not when the tag lands."""
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, propagate_to_roles=False)
    db.session.add_all([group, role, tag, user])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()
    role_id, user_id = role.id, user.id

    await ModifyRoleGroups(role_group=role_id, groups_to_add=[group.id], sync_to_okta=False).execute()

    db.session.expire_all()
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    assert membership.ended_at is None


async def test_attaching_a_role_to_an_untagged_group_leaves_members_alone(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """The common case must not acquire a cap -- or a wasted query pass -- just
    because the capping call was added to this operation."""
    mocker.patch.object(okta, "add_user_to_group")
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    db.session.add_all([group, role, user])
    await db.session.commit()
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()
    role_id, user_id = role.id, user.id

    await ModifyRoleGroups(role_group=role_id, groups_to_add=[group.id], sync_to_okta=False).execute()

    db.session.expire_all()
    membership = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    assert membership.ended_at is None


async def test_attaching_a_role_does_not_leak_one_groups_limit_onto_another(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """`ModifyGroupsTimeLimit(groups=G, tags=T)` applies T to every group in G,
    so the groups added by one operation cannot be unioned into a single call.
    Attaching a role to a tagged group and an untagged group together must
    leave the untagged group's own direct grants indefinite."""
    mocker.patch.object(okta, "add_user_to_group")
    tagged_group = OktaGroupFactory.build()
    untagged_group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([tagged_group, untagged_group, role, tag, user])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=tagged_group.id, tag_id=tag.id))
    # A direct, indefinite grant in the untagged group -- nothing about this
    # operation should touch it.
    db.session.add(OktaUserGroupMember(group_id=untagged_group.id, user_id=user.id, is_owner=False))
    await db.session.commit()
    untagged_group_id, user_id = untagged_group.id, user.id

    await ModifyRoleGroups(
        role_group=role.id,
        groups_to_add=[tagged_group.id, untagged_group.id],
        sync_to_okta=False,
    ).execute()

    db.session.expire_all()
    direct = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == untagged_group_id)
            .where(OktaUserGroupMember.user_id == user_id)
            .where(OktaUserGroupMember.role_group_map_id.is_(None))
        )
    ).one()
    assert direct.ended_at is None


# --- A constraint reaching a role must behave the same however it got there --
#
# A time limit on a role caps two things: membership *of* the role, and the
# access that membership grants *through* the role to every group the role is
# associated with. Both hold however the limit reaches the role -- a tag on the
# role itself, or one propagating from a group it is associated with. Capping
# only the first leaves a user whose role membership has just been shortened
# still holding derived access to the role's other groups, outliving the
# membership it exists because of.


async def test_propagated_limit_also_caps_access_the_role_grants(db: Db, mocker: MockerFixture, user: OktaUser) -> None:
    mocker.patch.object(okta, "add_user_to_group")
    tagged_group = OktaGroupFactory.build()
    other_group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY})
    db.session.add_all([tagged_group, other_group, role, tag, user])
    await db.session.commit()

    # The role is a member of both groups, and the user is an indefinite
    # member of the role, so their access to `other_group` is derived.
    member_map = RoleGroupMap(group_id=tagged_group.id, role_group_id=role.id, is_owner=False)
    other_map = RoleGroupMap(group_id=other_group.id, role_group_id=role.id, is_owner=False)
    db.session.add_all([member_map, other_map])
    await db.session.commit()
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()
    derived = OktaUserGroupMember(
        group_id=other_group.id, user_id=user.id, is_owner=False, role_group_map_id=other_map.id
    )
    db.session.add(derived)
    await db.session.commit()
    role_id, other_group_id, user_id = role.id, other_group.id, user.id

    db.session.add(OktaGroupTagMapFactory.build(group_id=tagged_group.id, tag_id=tag.id))
    await db.session.commit()
    await ModifyGroupsTimeLimit(groups=[tagged_group.id], tags=[tag.id]).execute()

    db.session.expire_all()
    in_role = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    granted_by_role = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == other_group_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()

    assert in_role.ended_at is not None
    # The access the role grants must not outlive the membership that confers it.
    assert granted_by_role.ended_at is not None
    assert granted_by_role.ended_at <= in_role.ended_at


async def test_attaching_a_role_also_caps_access_it_already_grants(
    db: Db, mocker: MockerFixture, user: OktaUser
) -> None:
    """The attach path has to reach as far as the tag-landing path. Capping the
    role's memberships alone would leave the access those memberships already
    confer elsewhere on its old end date."""
    mocker.patch.object(okta, "add_user_to_group")
    tagged_group = OktaGroupFactory.build()
    other_group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: ONE_DAY})
    db.session.add_all([tagged_group, other_group, role, tag, user])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=tagged_group.id, tag_id=tag.id))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=False))
    await db.session.commit()

    # The role already confers access to `other_group`, indefinitely.
    other_map = RoleGroupMap(group_id=other_group.id, role_group_id=role.id, is_owner=False)
    db.session.add(other_map)
    await db.session.commit()
    db.session.add(
        OktaUserGroupMember(group_id=other_group.id, user_id=user.id, is_owner=False, role_group_map_id=other_map.id)
    )
    await db.session.commit()
    role_id, other_group_id, user_id = role.id, other_group.id, user.id

    await ModifyRoleGroups(role_group=role_id, groups_to_add=[tagged_group.id], sync_to_okta=False).execute()

    db.session.expire_all()
    in_role = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == role_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()
    granted_by_role = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == other_group_id)
            .where(OktaUserGroupMember.user_id == user_id)
        )
    ).one()

    assert in_role.ended_at is not None
    assert granted_by_role.ended_at is not None
    assert granted_by_role.ended_at <= in_role.ended_at
