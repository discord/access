from sqlalchemy import select

from api.extensions import Db
from api.integrity import cap_role_memberships
from api.models import OktaUserGroupMember, RoleGroupMap, Tag
from tests.factories import (
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    OktaUserFactory,
    RoleGroupFactory,
    TagFactory,
)


async def _uncapped_role_membership(db: Db, *, propagate: bool = True, grant_is_owner: bool = False) -> tuple[str, str]:
    """A role that is a member of a tagged, member-time-limited group, holding
    one uncapped user grant. `grant_is_owner` makes that grant an *ownership*
    of the role rather than a membership."""
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, propagate_to_roles=propagate)
    user = OktaUserFactory.build()
    db.session.add_all([group, role, tag, user])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    db.session.add(OktaUserGroupMember(group_id=role.id, user_id=user.id, is_owner=grant_is_owner))
    await db.session.commit()
    return role.id, user.id


async def _ended_at(db: Db, role_id: str, user_id: str):
    db.session.expire_all()
    return (
        (
            await db.session.scalars(
                select(OktaUserGroupMember)
                .where(OktaUserGroupMember.group_id == role_id)
                .where(OktaUserGroupMember.user_id == user_id)
            )
        )
        .one()
        .ended_at
    )


async def test_sweep_caps_preexisting_grants(db: Db) -> None:
    role_id, user_id = await _uncapped_role_membership(db)
    capped = await cap_role_memberships(dry_run=False)
    assert capped == 1
    assert await _ended_at(db, role_id, user_id) is not None


async def test_dry_run_writes_nothing(db: Db) -> None:
    role_id, user_id = await _uncapped_role_membership(db)
    capped = await cap_role_memberships(dry_run=True)
    assert capped == 1
    assert await _ended_at(db, role_id, user_id) is None


async def test_sweep_is_idempotent(db: Db) -> None:
    await _uncapped_role_membership(db)
    assert await cap_role_memberships(dry_run=False) == 1
    assert await cap_role_memberships(dry_run=False) == 0


async def test_sweep_respects_the_gate(db: Db) -> None:
    role_id, user_id = await _uncapped_role_membership(db, propagate=False)
    assert await cap_role_memberships(dry_run=False) == 0
    assert await _ended_at(db, role_id, user_id) is None


async def test_sweep_leaves_role_owners_alone(db: Db) -> None:
    """Owning a role confers none of the role's grants, so nothing propagates
    onto a role's owner side -- `OWNER_SIDE_COUNTERPART` has no entry mapping
    *to* a role's own owner constraints. The sweep must therefore cap only
    `is_owner=False` grants; capping role owners would silently expire the
    very people who administer the role.

    Same fixture as `test_sweep_caps_preexisting_grants` except the grant is
    an ownership, so the only difference in outcome is the `is_owner` filter."""
    role_id, user_id = await _uncapped_role_membership(db, grant_is_owner=True)
    assert await cap_role_memberships(dry_run=False) == 0
    assert await _ended_at(db, role_id, user_id) is None
