from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient

from fastapi import FastAPI

from api.extensions import Db
from api.models import OktaGroup, RoleGroup, RoleGroupMap, Tag
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    AppTagMapFactory,
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    RoleGroupFactory,
    TagFactory,
)

_PAST = datetime.now(UTC) - timedelta(days=1)


async def _build_propagation_scenario(
    db: Db,
    *,
    group_deleted_at: datetime | None = None,
    group_is_managed: bool = True,
    role_deleted_at: datetime | None = None,
    tag_map_ended_at: datetime | None = None,
    role_map_ended_at: datetime | None = None,
) -> tuple[OktaGroup, RoleGroup, Tag]:
    """Builds the shared happy-path propagation scenario -- a managed,
    non-deleted source group tagged via an active `OktaGroupTagMap`, mapped
    to a non-deleted role via an active `RoleGroupMap` -- with knobs to break
    exactly one of the five active-record conditions `get_tag` filters on.
    With all defaults, the role WOULD appear in `propagated_to_groups`."""
    group = OktaGroupFactory.build(deleted_at=group_deleted_at, is_managed=group_is_managed)
    role = RoleGroupFactory.build(deleted_at=role_deleted_at)
    tag = TagFactory.build(name="SOX", constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id, ended_at=tag_map_ended_at))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False, ended_at=role_map_ended_at))
    await db.session.commit()
    return group, role, tag


async def test_group_detail_exposes_effective_constraints(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(name="SOX", constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    response = await client.get(url_for("group_by_id", group_id=role.id))
    assert response.status_code == 200
    constraints = response.json()["effective_constraints"]
    assert len(constraints) == 1
    assert constraints[0]["constraint"] == Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY
    assert constraints[0]["value"] == 86400
    assert constraints[0]["sources"][0]["tag_name"] == "SOX"
    assert constraints[0]["sources"][0]["origin"] == "member_association"


async def test_group_detail_effective_constraints_empty_when_untagged(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    group = OktaGroupFactory.build()
    db.session.add(group)
    await db.session.commit()

    response = await client.get(url_for("group_by_id", group_id=group.id))
    assert response.status_code == 200
    assert response.json()["effective_constraints"] == []


async def test_group_detail_direct_tag_has_direct_origin(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """A tag applied straight to the *fetched* group (no `AppTagMap` linkage)
    is reported with `origin == "direct"`. This walks `_own_tag_sources` on
    the group `GET /api/groups/{id}` actually loads -- unlike
    `test_group_detail_exposes_effective_constraints` above, which fetches a
    *role* and only ever reads tags via `_propagated_sources` on the
    associated group, a path that never touches
    `OktaGroupTagMap.active_app_tag_mapping`. Removing
    `joinedload(OktaGroupTagMap.active_app_tag_mapping)` from
    `group_tag_map_options()` should make this raise `InvalidRequestError`."""
    group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()

    response = await client.get(url_for("group_by_id", group_id=group.id))
    assert response.status_code == 200
    constraints = response.json()["effective_constraints"]
    assert len(constraints) == 1
    assert constraints[0]["sources"][0]["origin"] == "direct"


async def test_group_detail_app_tag_has_app_origin(app: FastAPI, client: AsyncClient, db: Db, url_for: Any) -> None:
    """A tag applied to an `App` and inherited by one of its `AppGroup`s is
    reported with `origin == "app"` when the `AppGroup` itself is fetched --
    the `_own_tag_sources` branch where `tag_map.active_app_tag_mapping is
    not None`. Setup mirrors
    `test_effective_constraints_app_tag_has_app_origin` in
    `tests/test_effective_constraints.py`: the inherited group-tag row
    (`OktaGroupTagMap`) points at the `AppTagMap` row via `app_tag_map_id`,
    which is what makes `active_app_tag_mapping` non-null. Removing
    `joinedload(OktaGroupTagMap.active_app_tag_mapping)` from
    `group_tag_map_options()` should make this raise `InvalidRequestError`."""
    app_row = AppFactory.build()
    app_group = AppGroupFactory.build()
    app_group.app_id = app_row.id
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([app_row, app_group, tag])
    await db.session.commit()
    app_tag_map = AppTagMapFactory.build(app_id=app_row.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    await db.session.commit()

    response = await client.get(url_for("group_by_id", group_id=app_group.id))
    assert response.status_code == 200
    constraints = response.json()["effective_constraints"]
    assert len(constraints) == 1
    assert constraints[0]["sources"][0]["origin"] == "app"


async def test_tag_detail_lists_roles_reached_by_propagation(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(name="SOX", constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.status_code == 200
    propagated = response.json()["propagated_to_groups"]
    assert len(propagated) == 1
    assert propagated[0]["group_id"] == role.id
    assert propagated[0]["source_group_id"] == group.id
    assert propagated[0]["origin"] == "member_association"


async def test_tag_detail_omits_propagation_when_gated_off(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(name="SOX", propagate_to_roles=False)
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.json()["propagated_to_groups"] == []


async def test_tag_detail_excludes_ended_group_tag_map(app: FastAPI, client: AsyncClient, db: Db, url_for: Any) -> None:
    """The tag was on the source group but was removed -- the `OktaGroupTagMap`
    row has an `ended_at` in the past. The role must not be reported as
    reached by propagation."""
    _group, _role, tag = await _build_propagation_scenario(db, tag_map_ended_at=_PAST)

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.status_code == 200
    assert response.json()["propagated_to_groups"] == []


async def test_tag_detail_excludes_ended_role_group_map(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """The role was once a member of the source group but that membership has
    ended -- the `RoleGroupMap` row has an `ended_at` in the past. The role
    must not be reported as reached by propagation."""
    _group, _role, tag = await _build_propagation_scenario(db, role_map_ended_at=_PAST)

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.status_code == 200
    assert response.json()["propagated_to_groups"] == []


async def test_tag_detail_excludes_soft_deleted_source_group(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """The source group carrying the tag has been soft-deleted. Even though
    its `OktaGroupTagMap` and `RoleGroupMap` rows are still active, a deleted
    source group must not surface a propagated role."""
    _group, _role, tag = await _build_propagation_scenario(db, group_deleted_at=_PAST)

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.status_code == 200
    assert response.json()["propagated_to_groups"] == []


async def test_tag_detail_excludes_unmanaged_source_group(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """Tags cannot apply to externally managed (`is_managed=False`) groups, so
    a role reached only through an unmanaged source group must not be
    reported as propagated to."""
    _group, _role, tag = await _build_propagation_scenario(db, group_is_managed=False)

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.status_code == 200
    assert response.json()["propagated_to_groups"] == []


async def test_tag_detail_excludes_soft_deleted_role(app: FastAPI, client: AsyncClient, db: Db, url_for: Any) -> None:
    """The role itself has been soft-deleted. Even though the source group is
    still tagged and the `RoleGroupMap` is still active, a deleted role must
    not be reported as reached by propagation."""
    _group, _role, tag = await _build_propagation_scenario(db, role_deleted_at=_PAST)

    response = await client.get(url_for("tag_by_id", tag_id=tag.id))
    assert response.status_code == 200
    assert response.json()["propagated_to_groups"] == []
