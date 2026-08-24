from typing import Any

from httpx import AsyncClient

from fastapi import FastAPI

from sqlalchemy import select
from api.extensions import Db
from api.models import Tag
from tests.factories import TagFactory


async def test_tag_defaults_to_propagating(db: Db) -> None:
    tag = TagFactory.build(name="SOX")
    db.session.add(tag)
    await db.session.commit()

    loaded = (await db.session.scalars(select(Tag).where(Tag.id == tag.id))).one()
    assert loaded.propagate_to_roles is True


async def test_put_tag_round_trips_propagate_to_roles(app: FastAPI, client: AsyncClient, db: Db, url_for: Any) -> None:
    tag = TagFactory.build(name="SOX")
    db.session.add(tag)
    await db.session.commit()
    tag_id = tag.id

    response = await client.put(
        url_for("tag_by_id_put", tag_id=tag_id),
        json={"propagate_to_roles": False},
    )
    assert response.status_code == 200
    assert response.json()["propagate_to_roles"] is False

    db.session.expire_all()
    loaded = (await db.session.scalars(select(Tag).where(Tag.id == tag_id))).one()
    assert loaded.propagate_to_roles is False


async def test_put_tag_leaves_propagate_to_roles_alone_when_absent(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """A tag edit that does not mention the field must not reset it."""
    tag = TagFactory.build(name="SOX", propagate_to_roles=False)
    db.session.add(tag)
    await db.session.commit()

    response = await client.put(
        url_for("tag_by_id_put", tag_id=tag.id),
        json={"description": "updated"},
    )
    assert response.status_code == 200
    assert response.json()["propagate_to_roles"] is False
