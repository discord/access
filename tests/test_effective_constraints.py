from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.extensions import Db
from api.models import AppTagMap, OktaGroup, OktaGroupTagMap, RoleGroup, RoleGroupMap, Tag
from api.models.tag import (
    constraint_source_clause,
    constraint_sources,
    effective_constraint,
    effective_ended_at,
)
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


async def _load_group_with_provenance(db: Db, group_id: str) -> OktaGroup:
    """Load a group with everything `effective_constraints` reads, including
    `active_app_tag_mapping` -- the relationship `_own_tag_sources` uses to
    distinguish a tag applied directly to the group ("direct") from one
    inherited via the group's `App` ("app"). `_load_role` above does NOT load
    this relationship, since `effective_constraint` (no provenance) never
    reads it; calling `effective_constraints` on a group loaded via
    `_load_role` would raise on this `lazy="raise_on_sql"` relationship.

    Also loads `AppTagMap.active_app`, matching `group_tag_map_options()` in
    `api/routers/_eager.py` -- `_own_tag_sources` reads it to populate
    `source_name` for an "app" origin."""
    return (
        await db.session.scalars(
            select(OktaGroup)
            .options(
                selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                selectinload(OktaGroup.active_group_tags)
                .joinedload(OktaGroupTagMap.active_app_tag_mapping)
                .joinedload(AppTagMap.active_app),
            )
            .where(OktaGroup.id == group_id)
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


async def test_constraint_source_clause_names_the_member_association(db: Db) -> None:
    role = await _setup(db, constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True}, is_owner=False)
    clause = constraint_source_clause(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, role)
    assert "which this role is a member of" in clause
    (source,) = constraint_sources(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, role)
    assert source.source_name in clause


async def test_constraint_source_clause_names_the_owner_association(db: Db) -> None:
    role = await _setup(db, constraints={Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True}, is_owner=True)
    clause = constraint_source_clause(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, role)
    assert "which this role owns" in clause


async def test_constraint_source_clause_names_every_blocking_source(db: Db) -> None:
    """Each truthy source blocks independently, so the message names them all."""
    group_a = OktaGroupFactory.build()
    group_b = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True})
    db.session.add_all([group_a, group_b, role, tag])
    await db.session.commit()
    db.session.add_all(
        [
            OktaGroupTagMapFactory.build(group_id=group_a.id, tag_id=tag.id),
            OktaGroupTagMapFactory.build(group_id=group_b.id, tag_id=tag.id),
            RoleGroupMap(group_id=group_a.id, role_group_id=role.id, is_owner=False),
            RoleGroupMap(group_id=group_b.id, role_group_id=role.id, is_owner=False),
        ]
    )
    await db.session.commit()
    clause = constraint_source_clause(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, await _load_role(db, role.id))
    assert group_a.name in clause
    assert group_b.name in clause
    assert " and " in clause


async def test_constraint_source_clause_falls_back_to_the_group_itself(db: Db) -> None:
    group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True})
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    loaded = await _load_group_with_provenance(db, group.id)
    assert constraint_source_clause(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, loaded) == (
        "due to tags on this group"
    )


async def test_nothing_propagates_onto_an_unmanaged_role(db: Db) -> None:
    """An unmanaged role enforces nothing, so nothing may propagate onto it.

    Every enforcement path gates on `is_managed` separately. Without the gate
    inside `_propagated_sources` the read surface would advertise a limit and a
    self-add prohibition that nothing applies, and the two would disagree.
    """
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build(is_managed=False)
    tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400,
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True,
        },
    )
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    loaded = await _load_role(db, role.id)
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded) is None
    assert effective_constraint(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, loaded) is None
    # Enforcement agrees, which is the point -- the two must not diverge.
    assert effective_ended_at(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded, None) is None
