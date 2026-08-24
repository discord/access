from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.extensions import Db
from api.models import OktaGroup, OktaGroupTagMap, RoleGroup, RoleGroupMap, Tag
from api.models.tag import effective_constraint
from tests.factories import (
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    RoleGroupFactory,
    TagFactory,
)


async def _load_role(db: Db, role_id: str) -> RoleGroup:
    """Load a role with everything `effective_constraint` reads."""
    return (
        await db.session.scalars(
            select(RoleGroup)
            .options(
                selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                selectinload(RoleGroup.active_role_associated_group_member_mappings)
                .joinedload(RoleGroupMap.active_group)
                .selectinload(OktaGroup.active_group_tags)
                .joinedload(OktaGroupTagMap.active_tag),
                selectinload(RoleGroup.active_role_associated_group_owner_mappings)
                .joinedload(RoleGroupMap.active_group)
                .selectinload(OktaGroup.active_group_tags)
                .joinedload(OktaGroupTagMap.active_tag),
            )
            .where(RoleGroup.id == role_id)
        )
    ).one()


async def _setup(db: Db, *, constraints: dict, is_owner: bool, propagate: bool = True) -> RoleGroup:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints=constraints, propagate_to_roles=propagate)
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=is_owner))
    await db.session.commit()
    return await _load_role(db, role.id)


async def test_member_association_propagates_same_key(db: Db) -> None:
    role = await _setup(db, constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, is_owner=False)
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, role) == 86400


async def test_owner_association_propagates_counterpart_key(db: Db) -> None:
    """A role that OWNS a group reads that group's OWNER-side limit onto its
    own MEMBER-side constraint. This is the new control."""
    role = await _setup(db, constraints={Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 3600}, is_owner=True)
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, role) == 3600


async def test_owner_association_does_not_propagate_member_key(db: Db) -> None:
    role = await _setup(db, constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 3600}, is_owner=True)
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, role) is None


async def test_nothing_reaches_the_roles_owner_side(db: Db) -> None:
    role = await _setup(db, constraints={Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 3600}, is_owner=False)
    assert effective_constraint(Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, role) is None


async def test_gate_off_blocks_propagation(db: Db) -> None:
    role = await _setup(
        db,
        constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400},
        is_owner=False,
        propagate=False,
    )
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, role) is None


async def test_disabled_tag_contributes_nothing(db: Db) -> None:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, enabled=False)
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()
    loaded = await _load_role(db, role.id)
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded) is None


async def test_unmanaged_source_group_contributes_nothing(db: Db) -> None:
    group = OktaGroupFactory.build(is_managed=False)
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()
    loaded = await _load_role(db, role.id)
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded) is None


async def test_per_tag_not_global(db: Db) -> None:
    """One propagating and one non-propagating tag on the same group:
    only the propagating tag's constraints reach the role."""
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    propagating = TagFactory.build(
        name="propagating",
        constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400},
        propagate_to_roles=True,
    )
    blocked = TagFactory.build(
        name="blocked",
        constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 60},
        propagate_to_roles=False,
    )
    db.session.add_all([group, role, propagating, blocked])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=propagating.id))
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=blocked.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()
    loaded = await _load_role(db, role.id)
    # 60 would win a `min` coalesce -- it must not be considered at all.
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded) == 86400


async def test_non_role_group_reads_only_its_own_tags(db: Db) -> None:
    group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    loaded = (
        await db.session.scalars(
            select(OktaGroup)
            .options(selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag))
            .where(OktaGroup.id == group.id)
        )
    ).one()
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded) == 86400
