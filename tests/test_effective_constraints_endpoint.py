"""`GET /api/constraints/effective` -- the shared answer to "what constraints
apply here?"

The dialogs that let someone choose an access duration or type a justification
each re-derived this client-side: which tags are enabled, which reach a role
through its associations, and the min/OR across them. That is the backend's
`constraint_sources` logic copied into TypeScript, and it drifted -- it still
mirrored the unconditional propagation rules after `propagate_to_roles` made
them conditional. This endpoint is what lets display and enforcement read the
same code.

Two modes, because the callers have two shapes of input: a set of groups (most
dialogs), and a set of tags with no group at all (approving a group request,
where the group being tagged does not exist yet).
"""

from typing import Any

from httpx import AsyncClient

from api.extensions import Db
from api.models import RoleGroupMap, Tag
from tests.factories import (
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    RoleGroupFactory,
    TagFactory,
)

URL = "/api/constraints/effective"


def _by_key(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {entry["constraint"]: entry for entry in entries}


# --- Group mode -------------------------------------------------------------


async def test_returns_a_groups_own_constraints(client: AsyncClient, db: Db) -> None:
    group = OktaGroupFactory.build()
    tag = TagFactory.build(name="SOX", constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [group.id]})
    assert response.status_code == 200
    body = response.json()

    assert _by_key(body["coalesced"])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 86400
    assert _by_key(body["by_group"][group.id])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 86400


async def test_coalesces_across_groups_while_keeping_them_separate(client: AsyncClient, db: Db) -> None:
    """The roll-up is what a bulk dialog needs to bound one shared duration
    control; the per-group entries are what it needs to mark individual rows.
    Returning only the former would leave the caller coalescing again."""
    short_group = OktaGroupFactory.build()
    long_group = OktaGroupFactory.build()
    short_tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 3600})
    long_tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 999_999})
    db.session.add_all([short_group, long_group, short_tag, long_tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=short_group.id, tag_id=short_tag.id))
    db.session.add(OktaGroupTagMapFactory.build(group_id=long_group.id, tag_id=long_tag.id))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [short_group.id, long_group.id]})
    assert response.status_code == 200
    body = response.json()

    assert _by_key(body["coalesced"])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 3600
    assert _by_key(body["by_group"][short_group.id])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 3600
    assert _by_key(body["by_group"][long_group.id])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 999_999


async def test_booleans_coalesce_by_or_across_groups(client: AsyncClient, db: Db) -> None:
    plain_group = OktaGroupFactory.build()
    strict_group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: True})
    db.session.add_all([plain_group, strict_group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=strict_group.id, tag_id=tag.id))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [plain_group.id, strict_group.id]})
    assert response.status_code == 200
    body = response.json()

    assert _by_key(body["coalesced"])[Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY]["value"] is True
    assert body["by_group"][plain_group.id] == []


async def test_includes_constraints_propagated_to_a_role(client: AsyncClient, db: Db) -> None:
    """The reason this endpoint exists: a role's applicable constraints cannot
    be read off its own tags."""
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(name="SOX", constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [role.id]})
    assert response.status_code == 200
    entry = _by_key(response.json()["by_group"][role.id])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]
    assert entry["value"] == 86400
    assert entry["sources"][0]["origin"] == "member_association"
    assert entry["sources"][0]["source_name"] == group.name


async def test_respects_the_propagation_gate(client: AsyncClient, db: Db) -> None:
    group = OktaGroupFactory.build()
    role = RoleGroupFactory.build()
    tag = TagFactory.build(
        constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400},
        propagate_to_roles=False,
    )
    db.session.add_all([group, role, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    db.session.add(RoleGroupMap(group_id=group.id, role_group_id=role.id, is_owner=False))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [role.id]})
    assert response.status_code == 200
    assert response.json()["by_group"][role.id] == []


async def test_unknown_group_ids_are_omitted_rather_than_failing(client: AsyncClient, db: Db) -> None:
    """A bulk dialog's selection can name a group that was deleted between the
    list render and the request; that should not blank the whole answer."""
    group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [group.id, "n" * 20]})
    assert response.status_code == 200
    body = response.json()
    assert set(body["by_group"]) == {group.id}
    assert _by_key(body["coalesced"])[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 86400


async def test_flags_every_tag_turns_off_are_not_reported(client: AsyncClient, db: Db) -> None:
    """The tag form writes all four boolean keys on every save, so most tags
    carry several `False` flags. Neither the roll-up nor the per-group answer
    may report one: a dialog reading `disallow_self_add_membership` back would
    withhold a self-add the backend permits."""
    group = OktaGroupFactory.build()
    tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 3600,
            Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY: False,
            Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY: False,
        }
    )
    db.session.add_all([group, tag])
    await db.session.commit()
    db.session.add(OktaGroupTagMapFactory.build(group_id=group.id, tag_id=tag.id))
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [group.id]})
    assert response.status_code == 200
    body = response.json()
    assert list(_by_key(body["coalesced"])) == [Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]
    assert list(_by_key(body["by_group"][group.id])) == [Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]


# --- Tag mode ---------------------------------------------------------------


async def test_tag_mode_coalesces_the_given_tags(client: AsyncClient, db: Db) -> None:
    """Approving a group request means choosing tags for a group that does not
    exist yet, so there is no id to ask about -- only the tags themselves."""
    short_tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 3600})
    long_tag = TagFactory.build(
        constraints={
            Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 999_999,
            Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY: True,
        }
    )
    db.session.add_all([short_tag, long_tag])
    await db.session.commit()

    response = await client.get(URL, params={"tag_ids": [short_tag.id, long_tag.id]})
    assert response.status_code == 200
    body = response.json()

    coalesced = _by_key(body["coalesced"])
    assert coalesced[Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY]["value"] == 3600
    assert coalesced[Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY]["value"] is True
    # No group was named, so there is nothing to key per-group results by.
    assert body["by_group"] == {}


async def test_tag_mode_ignores_disabled_tags(client: AsyncClient, db: Db) -> None:
    disabled = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 3600}, enabled=False)
    db.session.add(disabled)
    await db.session.commit()

    response = await client.get(URL, params={"tag_ids": [disabled.id]})
    assert response.status_code == 200
    assert response.json()["coalesced"] == []


async def test_tag_mode_omits_flags_the_tags_turn_off(client: AsyncClient, db: Db) -> None:
    """Same rule with no group involved: an approver choosing tags for a group
    about to be created must not be told a flag applies when every chosen tag
    sets it to `False`."""
    tag = TagFactory.build(
        constraints={
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 3600,
            Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY: False,
        }
    )
    db.session.add(tag)
    await db.session.commit()

    response = await client.get(URL, params={"tag_ids": [tag.id]})
    assert response.status_code == 200
    assert list(_by_key(response.json()["coalesced"])) == [Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY]


# --- Input validation -------------------------------------------------------


async def test_rejects_naming_neither_groups_nor_tags(client: AsyncClient, db: Db) -> None:
    response = await client.get(URL)
    assert response.status_code == 400


async def test_rejects_naming_both_groups_and_tags(client: AsyncClient, db: Db) -> None:
    """The two modes answer different questions; combining them would silently
    invent a third."""
    group = OktaGroupFactory.build()
    tag = TagFactory.build(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 3600})
    db.session.add_all([group, tag])
    await db.session.commit()

    response = await client.get(URL, params={"group_ids": [group.id], "tag_ids": [tag.id]})
    assert response.status_code == 400


async def test_rejects_an_unbounded_number_of_ids(client: AsyncClient, db: Db) -> None:
    """The eager loads scale with the requested set, so the set is bounded.

    Rejected by the query model, so this arrives as a validation error -- 400
    with an `errors` list, per this API's problem-detail contract -- rather
    than the router's own "exactly one of" 400.
    """
    response = await client.get(URL, params={"group_ids": [f"{i:020d}" for i in range(201)]})
    assert response.status_code == 400
    assert "errors" in response.json()
