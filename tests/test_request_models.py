"""Pydantic-level tests for the request body models in
`api/schemas/requests_schemas.py`. These pin the body-validation invariants
that used to live in per-router helpers: empty/missing names rejected at the
model layer, description length capped, REQUIRE_DESCRIPTIONS enforced, tag
constraints validated against `Tag.CONSTRAINTS`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.config import settings
from api.schemas.requests_schemas import (
    CreateAppBody,
    CreateTagBody,
    UpdateAppBody,
    UpdateTagBody,
)


# --- CreateAppBody / UpdateAppBody ------------------------------------------


def test_create_app_body_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        CreateAppBody.model_validate({"name": ""})


def test_create_app_body_rejects_overlong_description() -> None:
    with pytest.raises(ValidationError):
        CreateAppBody.model_validate({"name": "MyApp", "description": "x" * 1025})


def test_create_app_body_accepts_short_description() -> None:
    body = CreateAppBody.model_validate({"name": "MyApp", "description": "ok"})
    assert body.name == "MyApp"
    assert body.description == "ok"


def test_create_app_body_require_descriptions_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    with pytest.raises(ValidationError):
        CreateAppBody.model_validate({"name": "MyApp"})
    with pytest.raises(ValidationError):
        CreateAppBody.model_validate({"name": "MyApp", "description": ""})
    # Non-empty description satisfies the requirement.
    CreateAppBody.model_validate({"name": "MyApp", "description": "an app"})


def test_update_app_body_partial_skips_description_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    # `description` not in payload — partial update must not fire the check.
    UpdateAppBody.model_validate({"name": "Renamed"})


def test_update_app_body_explicit_empty_description_rejected_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    with pytest.raises(ValidationError):
        UpdateAppBody.model_validate({"description": ""})


def test_create_app_body_initial_app_group_prefix_required() -> None:
    body = {
        "name": "MyApp",
        "initial_additional_app_groups": [{"name": "wrong-prefix-Group"}],
    }
    with pytest.raises(ValidationError):
        CreateAppBody.model_validate(body)


def test_create_app_body_initial_app_group_owner_suffix_rejected() -> None:
    # Owner-suffix collision: cannot specify the owners group as additional.
    body = {
        "name": "MyApp",
        "initial_additional_app_groups": [{"name": "App-MyApp-Owners"}],
    }
    with pytest.raises(ValidationError):
        CreateAppBody.model_validate(body)


def test_create_app_body_initial_app_group_valid() -> None:
    body = {
        "name": "MyApp",
        "initial_additional_app_groups": [{"name": "App-MyApp-Engineers"}],
    }
    parsed = CreateAppBody.model_validate(body)
    assert parsed.initial_additional_app_groups is not None
    assert parsed.initial_additional_app_groups[0].name == "App-MyApp-Engineers"


# --- CreateTagBody / UpdateTagBody ------------------------------------------


def test_create_tag_body_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        CreateTagBody.model_validate({"name": ""})


def test_create_tag_body_rejects_unknown_constraint() -> None:
    with pytest.raises(ValidationError):
        CreateTagBody.model_validate({"name": "tag", "constraints": {"not_a_real_constraint": True}})


def test_create_tag_body_rejects_invalid_constraint_value() -> None:
    # `disallow_self_add_*` constraints expect a bool — passing a string fails.
    with pytest.raises(ValidationError):
        CreateTagBody.model_validate(
            {"name": "tag", "constraints": {"disallow_self_add_ownership": "not-a-bool"}}
        )


def test_create_tag_body_accepts_known_constraint() -> None:
    body = CreateTagBody.model_validate(
        {"name": "tag", "constraints": {"disallow_self_add_ownership": True}}
    )
    assert body.constraints == {"disallow_self_add_ownership": True}


def test_update_tag_body_partial_skips_description_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    UpdateTagBody.model_validate({"enabled": False})


def test_update_tag_body_unknown_constraint_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateTagBody.model_validate({"constraints": {"bogus": 1}})
