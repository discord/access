from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.extensions import Db
from api.models import AppTagMap, OktaGroup, OktaGroupTagMap, RoleGroup, RoleGroupMap, Tag
from api.models.tag import blocking_source, constraint_source_clause, effective_constraint, effective_constraints
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


async def test_effective_constraints_omits_unset_constraints(db: Db) -> None:
    role = await _setup(db, constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400}, is_owner=False)
    result = effective_constraints(role)
    assert [entry["constraint"] for entry in result] == [Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]
    assert result[0]["value"] == 86400
    assert result[0]["name"] == Tag.CONSTRAINTS[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY].name


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
    assert source["source_id"] is None


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


async def test_blocking_source_names_the_member_association(db: Db) -> None:
    role = await _setup(db, constraints={Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: True}, is_owner=False)
    source = blocking_source(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, role)
    assert source is not None
    assert source.origin == "member_association"
    assert "which this role is a member of" in constraint_source_clause(source)
    assert source.source_name in constraint_source_clause(source)


async def test_blocking_source_names_the_owner_association(db: Db) -> None:
    role = await _setup(db, constraints={Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: True}, is_owner=True)
    source = blocking_source(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, role)
    assert source is not None
    assert source.origin == "owner_association"
    assert "which this role owns" in constraint_source_clause(source)


async def test_constraint_source_clause_falls_back_for_direct_tags(db: Db) -> None:
    assert constraint_source_clause(None) == "due to group tags"
