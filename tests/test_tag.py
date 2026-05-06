import json
import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from fastapi import FastAPI

from api.config import settings
from api.extensions import Db
from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, Tag
from tests.factories import TagFactory


def test_get_tag(
    client: TestClient, db: Db, tag: Tag, access_app: App, app_group: AppGroup, okta_group: OktaGroup, url_for: Any
) -> None:
    # test 404
    tag_url = url_for("api-tags.tag_by_id", tag_id="randomid")
    rep = client.get(tag_url)
    assert rep.status_code == 404

    db.session.add(tag)
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    # test get tag
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)
    rep = client.get(tag_url)
    assert rep.status_code == 200

    data = rep.json()
    assert data["name"] == tag.name


def test_put_tag(
    client: TestClient, db: Db, tag: Tag, access_app: App, app_group: AppGroup, okta_group: OktaGroup, url_for: Any
) -> None:
    # test 404 — with a valid (empty) body shape so the path resolution
    # runs ahead of body-validation rejection.
    tag_url = url_for("api-tags.tag_by_id", tag_id="randomid")
    rep = client.put(tag_url, json={})
    assert rep.status_code == 404

    db.session.add(tag)
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    data = {
        "name": "Updated",
        "description": "new description",
        "enabled": False,
        "constraints": {Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 9999},
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.json()
    assert data["name"] == "Updated"
    assert data["description"] == "new description"
    assert data["enabled"] is False
    assert data["constraints"] == {Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 9999}
    assert data["id"] == tag.id

    data = {
        "name": "Updated-again",
        "description": "new description",
        "enabled": True,
        "constraints": {},
    }
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200

    data = rep.json()
    assert data["name"] == "Updated-again"
    assert data["description"] == "new description"
    assert data["enabled"] is True
    assert data["constraints"] == {}
    assert data["id"] == tag.id

    data = {
        "constraints": {"invalid_constraint": "asdfas"},
    }
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 400

    data = {
        "constraints": {"time_limit": "asdfas"},
    }
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 400


def test_delete_tag(
    client: TestClient, db: Db, tag: Tag, access_app: App, app_group: AppGroup, okta_group: OktaGroup, url_for: Any
) -> None:
    # test 404
    tag_url = url_for("api-tags.tag_by_id", tag_id="randomid")
    rep = client.delete(tag_url)
    assert rep.status_code == 404

    db.session.add(tag)
    db.session.add(access_app)
    db.session.add(okta_group)
    db.session.commit()
    app_group.app_id = access_app.id
    db.session.add(app_group)
    db.session.commit()

    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    app_tag_map = AppTagMap(app_id=access_app.id, tag_id=tag.id)
    db.session.add(app_tag_map)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=app_group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id))
    db.session.commit()

    tag_id = tag.id
    tag_url = url_for("api-tags.tag_by_id", tag_id=tag_id)
    rep = client.delete(tag_url)
    assert rep.status_code == 200
    assert db.session.get(Tag, tag_id).deleted_at is not None


def test_create_tag(client: TestClient, db: Db, mocker: MockerFixture, url_for: Any) -> None:
    # test bad data
    tags_url = url_for("api-tags.tags")
    data: dict[str, Any] = {}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 400

    data = {"name": "Created", "description": ""}

    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201

    data = rep.json()
    tag = db.session.get(Tag, data["id"])

    assert tag.name == "Created"
    assert tag.description == ""
    assert tag.enabled is True
    assert tag.constraints == {}

    data = {"name": "Created2", "description": "", "enabled": False, "constraints": {}}

    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201

    data = rep.json()
    tag = db.session.get(Tag, data["id"])

    assert tag.name == "Created2"
    assert tag.description == ""
    assert tag.enabled is False
    assert tag.constraints == {}


def test_get_all_tag(client: TestClient, db: Db, url_for: Any) -> None:
    tags_url = url_for("api-tags.tags")

    tags = TagFactory.create_batch(3)
    db.session.add_all(tags)
    db.session.commit()

    rep = client.get(tags_url)
    assert rep.status_code == 200

    results = rep.json()
    for tag in tags:
        assert any(u["id"] == tag.id for u in results["results"])

    rep = client.get(tags_url, params={"q": "Tag-"})
    assert rep.status_code == 200

    results = rep.json()
    assert len(results["results"]) == 3
    for tag in tags:
        assert any(u["id"] == tag.id for u in results["results"])


@pytest.mark.parametrize("app", [False, True], indirect=True)
def test_create_tag_with_and_without_description(
    app: FastAPI, client: TestClient, db: Db, mocker: MockerFixture, url_for: Any
) -> None:
    """Test that tags work with or without descriptions based on REQUIRE_DESCRIPTIONS setting"""
    require_descriptions = settings.REQUIRE_DESCRIPTIONS

    tags_url = url_for("api-tags.tags")

    # Test creating tag without description
    data: dict[str, Any] = {"name": "TestTag"}
    rep = client.post(tags_url, json=data)
    if require_descriptions:
        # Should fail when REQUIRE_DESCRIPTIONS=True
        assert rep.status_code == 400
        response_data = rep.json()
        assert "required" in str(response_data).lower()
    else:
        # Should succeed with backwards compatibility
        assert rep.status_code == 201
        result = rep.json()
        tag = db.session.get(Tag, result["id"])
        assert tag.name == "TestTag"
        assert tag.description == ""

    # Test creating tag with empty description
    data = {"name": "TestTag2", "description": ""}
    rep = client.post(tags_url, json=data)
    if require_descriptions:
        # Should fail - empty description fails length validation
        assert rep.status_code == 400
        response_data = rep.json()
        assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()
    else:
        # Should succeed with empty description
        assert rep.status_code == 201
        result = rep.json()
        tag = db.session.get(Tag, result["id"])
        assert tag.name == "TestTag2"
        assert tag.description == ""

    # Test creating tag with a description should always succeed
    data = {"name": "TestTag3", "description": "This has a description"}
    rep = client.post(tags_url, json=data)
    assert rep.status_code == 201

    result = rep.json()
    tag = db.session.get(Tag, result["id"])
    assert tag.name == "TestTag3"
    assert tag.description == "This has a description"


@pytest.mark.parametrize("app", [False, True], indirect=True)
def test_partial_tag_update_preserves_description(
    app: FastAPI, client: TestClient, db: Db, mocker: MockerFixture, tag: Tag, url_for: Any
) -> None:
    """Test that tag updates handle descriptions correctly based on REQUIRE_DESCRIPTIONS setting"""
    require_descriptions = settings.REQUIRE_DESCRIPTIONS

    # Set up the tag with a description
    tag.description = "Original description"
    db.session.add(tag)
    db.session.commit()

    tag_url = url_for("api-tags.tag_by_id", tag_id=tag.id)

    # Test updating with empty description
    data = {"name": "UpdatedWithEmpty", "description": ""}
    rep = client.put(tag_url, json=data)
    if require_descriptions:
        # Should fail - empty description fails length validation
        assert rep.status_code == 400
        response_data = rep.json()
        assert "description" in str(response_data).lower() or "characters" in str(response_data).lower()
    else:
        # Should succeed
        assert rep.status_code == 200
        result = rep.json()
        assert result["name"] == "UpdatedWithEmpty"
        assert result["description"] == ""

    # Reset the tag description back to "Original description" for the partial update test
    if not require_descriptions:
        # Need to reset since empty description succeeded above
        data = {"name": "UpdatedWithEmpty", "description": "Original description"}
        rep = client.put(tag_url, json=data)
        assert rep.status_code == 200

    # Test partial update without description should preserve existing description
    data = {"name": "UpdatedTag"}
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200
    result = rep.json()
    assert result["name"] == "UpdatedTag"
    assert result["description"] == "Original description"  # Description is preserved

    # Test updating with valid description should succeed
    data = {"name": "UpdatedTag2", "description": "Updated description"}
    rep = client.put(tag_url, json=data)
    assert rep.status_code == 200
    result = rep.json()
    assert result["name"] == "UpdatedTag2"
    assert result["description"] == "Updated description"


def test_post_tag_validation_via_http(client: TestClient, db: Db, url_for: Any) -> None:
    """Empty name, overlong description, unknown constraint key and bad
    constraint value all rejected at the HTTP layer."""
    tags_url = url_for("api-tags.tags")

    rep = client.post(tags_url, json={"name": ""})
    assert rep.status_code == 400

    rep = client.post(tags_url, json={"name": "good", "description": "x" * 1025})
    assert rep.status_code == 400

    rep = client.post(tags_url, json={"name": "good", "constraints": {"not_a_real_constraint": True}})
    assert rep.status_code == 400

    rep = client.post(
        tags_url, json={"name": "good", "constraints": {"disallow_self_add_ownership": "not-bool"}}
    )
    assert rep.status_code == 400


def test_post_tag_require_descriptions_via_http(
    client: TestClient, db: Db, url_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQUIRE_DESCRIPTIONS gates POST at the HTTP boundary."""
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    tags_url = url_for("api-tags.tags")
    rep = client.post(tags_url, json={"name": "needs-desc"})
    assert rep.status_code == 400


def test_post_tag_duplicate_name_blocked(client: TestClient, db: Db, url_for: Any) -> None:
    """Duplicate tag name on POST returns 400 (not silent 201)."""
    tags_url = url_for("api-tags.tags")
    rep = client.post(tags_url, json={"name": "DupTag"})
    assert rep.status_code == 201

    rep = client.post(tags_url, json={"name": "duptag"})  # case-insensitive
    assert rep.status_code == 400
    assert "already exists" in rep.text


def test_put_tag_rename_collision_blocked(client: TestClient, db: Db, url_for: Any) -> None:
    """Renaming a tag onto another tag's name is rejected with 400."""
    tag_a = TagFactory.create(name="OneTag")
    tag_b = TagFactory.create(name="OtherTag")
    db.session.add_all([tag_a, tag_b])
    db.session.commit()

    put_url = url_for("api-tags.tag_by_id_put", tag_id=tag_b.id)
    rep = client.put(put_url, json={"name": "onetag"})  # case-insensitive
    assert rep.status_code == 400
    assert "already exists" in rep.text


def test_get_tags_q_via_http(client: TestClient, db: Db, url_for: Any) -> None:
    """Q — `q` query param honored on /api/tags."""
    db.session.add_all([TagFactory.create(name="ZelaTagOne"), TagFactory.create(name="OtherTag")])
    db.session.commit()
    tags_url = url_for("api-tags.tags")
    rep = client.get(tags_url, params={"q": "ZelaTag"})
    assert rep.status_code == 200
    names = [t["name"] for t in rep.json()["results"]]
    assert "ZelaTagOne" in names
    assert "OtherTag" not in names


def test_put_tag_404_for_soft_deleted(client: TestClient, db: Db, tag: Tag, url_for: Any) -> None:
    """PUT on a soft-deleted tag returns 404."""
    tag.deleted_at = datetime.now(timezone.utc)
    db.session.add(tag)
    db.session.commit()

    tag_url = url_for("api-tags.tag_by_id_put", tag_id=tag.id)
    rep = client.put(tag_url, json={"name": "RenamedAfterDelete"})
    assert rep.status_code == 404


def test_delete_tag_404_for_soft_deleted(client: TestClient, db: Db, tag: Tag, url_for: Any) -> None:
    """DELETE on an already-soft-deleted tag returns 404."""
    tag.deleted_at = datetime.now(timezone.utc)
    db.session.add(tag)
    db.session.commit()

    tag_url = url_for("api-tags.tag_by_id_delete", tag_id=tag.id)
    rep = client.delete(tag_url)
    assert rep.status_code == 404


def test_put_tag_emits_tag_modify_audit_log(
    client: TestClient,
    db: Db,
    tag: Tag,
    url_for: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PUT /api/tags/{id} emits a `tag_modify` audit log carrying old/new state."""
    original_name = tag.name
    db.session.add(tag)
    db.session.commit()

    tag_url = url_for("api-tags.tag_by_id_put", tag_id=tag.id)
    with caplog.at_level(logging.INFO, logger="access.audit"):
        rep = client.put(tag_url, json={"name": "RenamedTag"})
        assert rep.status_code == 200

    audit_records = [r for r in caplog.records if r.name == "access.audit"]
    matching = []
    for record in audit_records:
        try:
            payload = json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if payload.get("event_type") == "TAG_MODIFY":
            matching.append(payload)

    assert len(matching) == 1, f"expected one TAG_MODIFY audit log, got {len(matching)}"
    payload = matching[0]
    assert payload["tag"]["id"] == tag.id
    assert payload["tag"]["name"] == "RenamedTag"
    assert payload["old_tag"]["name"] == original_name


def test_list_tags_search_matches_description(client: TestClient, db: Db, url_for: Any) -> None:
    """`q` matches against Tag.description as well as Tag.name."""
    name_only = TagFactory.create(name="HaystackTag", description="generic")
    desc_only = TagFactory.create(name="OtherName", description="contains needle here")
    db.session.add_all([name_only, desc_only])
    db.session.commit()

    tags_url = url_for("api-tags.tags")
    rep = client.get(tags_url, params={"q": "needle"})
    assert rep.status_code == 200
    ids = [t["id"] for t in rep.json()["results"]]
    assert desc_only.id in ids
    assert name_only.id not in ids


def test_list_tags_response_is_summary_shape(
    client: TestClient,
    db: Db,
    tag: Tag,
    okta_group: OktaGroup,
    url_for: Any,
) -> None:
    """List response uses `TagListItem` — no `active_group_tags`, no `deleted_at`."""
    db.session.add(tag)
    db.session.add(okta_group)
    db.session.commit()
    db.session.add(OktaGroupTagMap(group_id=okta_group.id, tag_id=tag.id))
    db.session.commit()

    tags_url = url_for("api-tags.tags")
    rep = client.get(tags_url)
    assert rep.status_code == 200
    items = rep.json()["results"]
    matched = [item for item in items if item["id"] == tag.id]
    assert len(matched) == 1
    item = matched[0]
    assert "active_group_tags" not in item
    assert "deleted_at" not in item
    expected_keys = {"id", "name", "description", "constraints", "enabled", "created_at", "updated_at"}
    assert set(item.keys()) == expected_keys


def test_get_tag_prefers_active_over_deleted_with_same_name(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """When two tags share the same name (one soft-deleted, one active),
    GET /api/tags/{name} must return the active one. The router uses
    `nullsfirst(deleted_at.desc())` to enforce that ordering."""
    deleted = TagFactory.create(name="DupName", description="old")
    deleted.deleted_at = datetime.now(timezone.utc)
    db.session.add(deleted)
    active = TagFactory.create(name="DupName", description="new")
    db.session.add(active)
    db.session.commit()

    tag_url = url_for("api-tags.tag_by_id", tag_id="DupName")
    rep = client.get(tag_url)
    assert rep.status_code == 200
    body = rep.json()
    assert body["id"] == active.id
    assert body["deleted_at"] is None


def test_get_tag_returns_deleted_when_no_active_match(
    client: TestClient, db: Db, url_for: Any
) -> None:
    """If only a soft-deleted tag with that name exists, GET still returns
    it (the `nullsfirst(deleted_at.desc())` ordering falls through to the
    deleted row when no active row exists)."""
    deleted = TagFactory.create(name="OnlyDeleted")
    deleted.deleted_at = datetime.now(timezone.utc)
    db.session.add(deleted)
    db.session.commit()

    tag_url = url_for("api-tags.tag_by_id", tag_id="OnlyDeleted")
    rep = client.get(tag_url)
    assert rep.status_code == 200
    assert rep.json()["id"] == deleted.id
