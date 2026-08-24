from pytest_mock import MockerFixture
from sqlalchemy import select

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, Tag
from api.operations import ModifyGroupUsers
from api.services import okta
from tests.factories import (
    OktaGroupFactory,
    OktaGroupTagMapFactory,
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
