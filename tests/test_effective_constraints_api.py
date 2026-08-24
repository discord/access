from typing import Any

from httpx import AsyncClient

from fastapi import FastAPI

from api.extensions import Db
from api.models import RoleGroupMap, Tag
from tests.factories import (
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
