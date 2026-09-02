from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.extensions import Db
from api.models import AppTagMap, OktaGroup, OktaGroupTagMap, RoleGroup, RoleGroupMap, Tag
from api.routers._eager import effective_constraint_options
from api.models.tag import (
    ConstraintOrigin,
    ConstraintSource,
    _constraint_entry,
    constraint_source_clause,
    constraint_sources,
    effective_constraint,
    effective_constraints,
    effective_ended_at,
)
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    AppTagMapFactory,
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    RoleGroupFactory,
    TagFactory,
)


async def _load_role(db: Db, role_id: str) -> RoleGroup:
    """Load a role with everything `effective_constraint` reads.

    Through the same builder the enforcement paths use, so a change to the
    loaders cannot pass here while breaking them. `select(RoleGroup)` needs no
    `selectin_polymorphic` pairing.
    """
    return (
        await db.session.scalars(
            select(RoleGroup).options(*effective_constraint_options()).where(RoleGroup.id == role_id)
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


async def test_soft_deleted_source_group_contributes_nothing(db: Db) -> None:
    """A mapping whose group has been soft-deleted while the mapping is still
    active reaches `_propagated_sources` with `active_group` as None.

    Reachable only under `selectinload`: `RoleGroupMap.active_group` carries
    `innerjoin=True`, so a `joinedload` drops the whole mapping instead and the
    None branch never runs. `effective_constraint_options` declares
    `selectinload`, so this is the path the enforcement sites take."""
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    group.deleted_at = datetime.now(UTC) - timedelta(days=1)
    db.session.add(group)
    await db.session.commit()
    role_id = role.id

    db.session.expire_all()
    loaded = await _load_role(db, role_id)
    (mapping,) = loaded.active_role_associated_group_member_mappings
    assert mapping.active_group is None
    assert effective_constraint(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, loaded) is None


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


async def test_effective_constraints_omits_unset_constraints(db: Db) -> None:
    role = await _setup(db, constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, is_owner=False)
    result = effective_constraints(role)
    assert [entry["constraint"] for entry in result] == [Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]
    assert result[0]["value"] == 86400
    assert result[0]["name"] == Tag.CONSTRAINTS[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY].name


async def test_effective_constraints_omits_a_flag_every_tag_turns_off(db: Db) -> None:
    """The tag form writes all four boolean keys on every save, so a tag whose
    only real setting is a time limit still carries four `False` flags. Those
    set nothing, and reporting them would tell a reader that a
    separation-of-duties control is in force when it is switched off."""
    group = OktaGroupFactory.build()
    tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400,
            Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: False,
            Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: False,
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        }
    )
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()

    loaded = await _load_group_with_provenance(db, group.id)
    assert [entry["constraint"] for entry in effective_constraints(loaded)] == [Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]


async def test_effective_constraints_omits_a_tag_that_declines_the_flag(db: Db) -> None:
    """One tag turning a flag on and another turning it off leaves it in force,
    but only the first is the reason. Naming the second would send a reader to
    edit a tag that already says `False`."""
    group = OktaGroupFactory.build()
    strict = TagFactory.build(name="Strict", constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True})
    lax = TagFactory.build(name="Lax", constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False})
    db.session.add_all([group, strict, lax])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=strict.id))
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=lax.id))
    await db.session.commit()

    loaded = await _load_group_with_provenance(db, group.id)
    (entry,) = effective_constraints(loaded)
    assert entry["value"] is True
    assert [source["tag_name"] for source in entry["sources"]] == ["Strict"]


async def test_effective_constraints_keeps_a_falsy_numeric_limit(db: Db) -> None:
    """The `False` filter is discriminated on the boolean, not on truthiness: a
    zero-second limit is the tightest possible constraint, not the absence of
    one. The API validator rejects it, so this guards the helper directly."""
    constraint = Tag.CONSTRAINTS[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]
    source = ConstraintSource(
        tag=TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 0}),
        value=0,
        origin=ConstraintOrigin.DIRECT,
        source_id=None,
        source_name=None,
    )
    entry = _constraint_entry(Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, constraint, [source])
    assert entry is not None
    assert entry["value"] == 0


async def test_effective_constraints_reports_association_source(db: Db) -> None:
    role = await _setup(db, constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, is_owner=False)
    (entry,) = effective_constraints(role)
    (source,) = entry["sources"]
    assert source["origin"] == "member_association"
    assert source["source_name"] is not None


async def test_effective_constraints_is_empty_when_nothing_applies(db: Db) -> None:
    role = RoleGroupFactory.build()
    db.session.add(role)
    await db.session.commit()
    loaded = await _load_role(db, role.id)
    assert effective_constraints(loaded) == []


async def test_effective_constraints_direct_tag_has_direct_origin(db: Db) -> None:
    """A tag applied straight to a group (no `AppTagMap` linkage) is reported
    with `origin == "direct"` -- the `_own_tag_sources` branch where
    `tag_map.active_app_tag_mapping is None`."""
    group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()
    loaded = await _load_group_with_provenance(db, group.id)
    (entry,) = effective_constraints(loaded)
    (source,) = entry["sources"]
    assert source["origin"] == "direct"


async def test_effective_constraints_app_tag_has_app_origin(db: Db) -> None:
    """A tag applied to an `App` and inherited by one of its `AppGroup`s is
    reported with `origin == "app"` -- the `_own_tag_sources` branch where
    `tag_map.active_app_tag_mapping is not None`. The inherited group-tag row
    (`OktaGroupTagMap`) points at the `AppTagMap` row via `app_tag_map_id`;
    that linkage is what makes `active_app_tag_mapping` non-null.

    The source also carries the app's name in `source_name` -- for an
    "app" origin the "source" is the App itself, not a group -- hence the
    origin-agnostic field names -- so `source_id` stays `None` while
    the name is still meaningful to show in the UI."""
    app = AppFactory.build()
    app_group = AppGroupFactory.build()
    app_group.app_id = app.id
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([app, app_group, tag])
    await db.session.commit()
    app_tag_map = AppTagMapFactory.build(app_id=app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    await db.session.commit()
    loaded = await _load_group_with_provenance(db, app_group.id)
    (entry,) = effective_constraints(loaded)
    (source,) = entry["sources"]
    assert source["origin"] == "app"
    assert source["source_name"] == app.name
    assert source["source_id"] == app.id


async def test_effective_constraints_tolerates_a_soft_deleted_app(db: Db) -> None:
    """`AppTagMap.active_app` filters `App.deleted_at`, so it resolves to
    `None` once the app is soft-deleted while an inherited `OktaGroupTagMap`
    row survives. Reading `.name` off it unguarded raises `AttributeError`
    (a 500); the source must instead report the "app" origin with no name.

    Loaded with `selectinload` deliberately. `group_tag_map_options()` uses
    `joinedload`, and `active_app` is `innerjoin=True`, so there the deleted
    app collapses the whole `active_app_tag_mapping` chain to `None` and the
    source degrades to "direct" instead -- which is why the unguarded read is
    not reachable through the production loader today. `selectinload` issues a
    separate SELECT per relationship, so `active_app_tag_mapping` survives
    while `active_app` comes back `None`: exactly the shape the guard exists
    for, and the shape any future switch to `selectinload` would produce."""
    app = AppFactory.build()
    app_group = AppGroupFactory.build()
    app_group.app_id = app.id
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([app, app_group, tag])
    await db.session.commit()
    app_tag_map = AppTagMapFactory.build(app_id=app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    await db.session.commit()

    app.deleted_at = datetime.now(UTC) - timedelta(days=1)
    db.session.add(app)
    await db.session.commit()
    app_group_id = app_group.id

    db.session.expire_all()
    loaded = (
        await db.session.scalars(
            select(OktaGroup)
            .options(
                selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                selectinload(OktaGroup.active_group_tags)
                .selectinload(OktaGroupTagMap.active_app_tag_mapping)
                .selectinload(AppTagMap.active_app),
            )
            .where(OktaGroup.id == app_group_id)
        )
    ).one()
    (entry,) = effective_constraints(loaded)
    (source,) = entry["sources"]
    assert source["origin"] == "app"
    assert source["source_name"] is None
