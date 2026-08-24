from typing import Any

from httpx import AsyncClient

from fastapi import FastAPI

from api.extensions import Db
from api.models import RoleGroupMap, Tag
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    AppTagMapFactory,
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    RoleGroupFactory,
    TagFactory,
)


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
