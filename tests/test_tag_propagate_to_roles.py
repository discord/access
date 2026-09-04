from typing import Any

import pytest
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


@pytest.mark.parametrize("propagate", [True, False])
async def test_post_tag_round_trips_explicit_propagate_to_roles(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, propagate: bool
) -> None:
    """`post_tag` translates the optional body field with a ternary (absent
    means the `True` default). Covering only the omitted case would leave the
    gate that decides whether a tag's constraints reach roles at all untested
    on the create path -- PUT is covered, POST was not."""
    response = await client.post(
        url_for("tags_create"),
        json={
            "name": f"SOX-{propagate}",
            "description": "test tag",
            "propagate_to_roles": propagate,
        },
    )
    assert response.status_code == 201
    assert response.json()["propagate_to_roles"] is propagate
    tag_id = response.json()["id"]

    db.session.expire_all()
    loaded = (await db.session.scalars(select(Tag).where(Tag.id == tag_id))).one()
    assert loaded.propagate_to_roles is propagate


async def test_post_tag_defaults_propagate_to_roles_when_absent(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """The other half of the same ternary: an omitted field must land on the
    documented `True` default, not on `None`/falsy."""
    response = await client.post(
        url_for("tags_create"),
        json={"name": "SOX-absent", "description": "test tag"},
    )
    assert response.status_code == 201
    assert response.json()["propagate_to_roles"] is True

    db.session.expire_all()
    loaded = (await db.session.scalars(select(Tag).where(Tag.id == response.json()["id"]))).one()
    assert loaded.propagate_to_roles is True


# --- Self-add constraints require propagation ------------------------------
#
# `disallow_self_add_*` is a separation-of-duties control, and unlike the
# reason and time-limit constraints it does not merely weaken when propagation
# is off -- it inverts to permitted: An owner of G blocked from adding
# themselves directly could add themselves to a role associated with G and arrive
# at the same access by a supported path. So the two are not independently
# configurable: a tag carrying either self-add key must propagate.


SELF_ADD_KEYS = [
    Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
    Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
]


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_post_tag_rejects_self_add_without_propagation(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, constraint_key: str
) -> None:
    response = await client.post(
        url_for("tags_create"),
        json={
            "name": f"SOX-post-{constraint_key}",
            "description": "test tag",
            "constraints": {constraint_key: True},
            "propagate_to_roles": False,
        },
    )
    assert response.status_code == 400
    assert constraint_key in response.json()["detail"]

    db.session.expire_all()
    assert (await db.session.scalars(select(Tag).where(Tag.name == f"SOX-post-{constraint_key}"))).first() is None


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_post_tag_allows_self_add_with_propagation(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, constraint_key: str
) -> None:
    response = await client.post(
        url_for("tags_create"),
        json={
            "name": f"SOX-ok-{constraint_key}",
            "description": "test tag",
            "constraints": {constraint_key: True},
            "propagate_to_roles": True,
        },
    )
    assert response.status_code == 201


async def test_post_tag_allows_disabled_propagation_without_self_add(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any
) -> None:
    """Reason and time-limit constraints degrade rather than invert, so they
    remain freely combinable with propagation turned off."""
    response = await client.post(
        url_for("tags_create"),
        json={
            "name": "SOX-reason-only",
            "description": "test tag",
            "constraints": {
                Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True,
                Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400,
            },
            "propagate_to_roles": False,
        },
    )
    assert response.status_code == 201


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_put_tag_rejects_turning_off_propagation_on_a_self_add_tag(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, constraint_key: str
) -> None:
    """The conflict comes from the stored constraints, which a body carrying
    only `propagate_to_roles` cannot see -- so this cannot be caught in the
    request schema."""
    tag = TagFactory.build(name=f"SOX-put-{constraint_key}", constraints={constraint_key: True})
    db.session.add(tag)
    await db.session.commit()
    tag_id = tag.id

    response = await client.put(
        url_for("tag_by_id_put", tag_id=tag_id),
        json={"propagate_to_roles": False},
    )
    assert response.status_code == 400

    db.session.expire_all()
    loaded = (await db.session.scalars(select(Tag).where(Tag.id == tag_id))).one()
    assert loaded.propagate_to_roles is True


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_put_tag_rejects_adding_self_add_to_a_non_propagating_tag(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, constraint_key: str
) -> None:
    """The mirror image: the body supplies the constraint and the stored row
    supplies the propagation setting."""
    tag = TagFactory.build(name=f"SOX-put-rev-{constraint_key}", propagate_to_roles=False)
    db.session.add(tag)
    await db.session.commit()
    tag_id = tag.id

    response = await client.put(
        url_for("tag_by_id_put", tag_id=tag_id),
        json={"constraints": {constraint_key: True}},
    )
    assert response.status_code == 400

    db.session.expire_all()
    loaded = (await db.session.scalars(select(Tag).where(Tag.id == tag_id))).one()
    assert loaded.constraints == {}


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_put_tag_allows_clearing_both_halves_together(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, constraint_key: str
) -> None:
    """Validation reads the merged result, not either half alone: dropping the
    constraint in the same request that turns propagation off is coherent."""
    tag = TagFactory.build(name=f"SOX-put-both-{constraint_key}", constraints={constraint_key: True})
    db.session.add(tag)
    await db.session.commit()
    tag_id = tag.id

    response = await client.put(
        url_for("tag_by_id_put", tag_id=tag_id),
        json={"constraints": {}, "propagate_to_roles": False},
    )
    assert response.status_code == 200

    db.session.expire_all()
    loaded = (await db.session.scalars(select(Tag).where(Tag.id == tag_id))).one()
    assert loaded.propagate_to_roles is False
    assert loaded.constraints == {}


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_post_tag_rejects_self_add_without_propagation_even_when_disabled(
    app: FastAPI, client: AsyncClient, db: Db, url_for: Any, constraint_key: str
) -> None:
    """A disabled tag enforces nothing today, but enabling it later goes
    through a body that need not mention either field. Validating regardless of
    `enabled` keeps that later flip from being the moment the hole opens."""
    response = await client.post(
        url_for("tags_create"),
        json={
            "name": f"SOX-disabled-{constraint_key}",
            "description": "test tag",
            "constraints": {constraint_key: True},
            "propagate_to_roles": False,
            "enabled": False,
        },
    )
    assert response.status_code == 400


@pytest.mark.parametrize("constraint_key", SELF_ADD_KEYS)
async def test_create_tag_operation_rejects_self_add_without_propagation(db: Db, constraint_key: str) -> None:
    """The operation guards the invariant too, so CLI and seed callers that
    never touch the router cannot write the combination either."""
    from api.exceptions import InvalidRequestError
    from api.operations import CreateTag

    tag = TagFactory.build(
        name=f"SOX-op-{constraint_key}",
        constraints={constraint_key: True},
        propagate_to_roles=False,
    )
    with pytest.raises(InvalidRequestError):
        await CreateTag(tag=tag).execute()
