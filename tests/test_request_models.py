"""Pydantic-level tests for the request body models in
`api/schemas/requests_schemas.py`. These pin the body-validation invariants
that used to live in per-router helpers: empty/missing names rejected at the
model layer, description length capped, REQUIRE_DESCRIPTIONS enforced, tag
constraints validated against `Tag.CONSTRAINTS`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from api.config import settings
from api.schemas.requests_schemas import (
    CreateAccessRequestBody,
    CreateAppBody,
    CreateRoleRequestBody,
    CreateTagBody,
    GroupMember,
    ResolveAccessRequestBody,
    ResolveGroupRequestBody,
    ResolveRoleRequestBody,
    RoleMember,
    UpdateAppBody,
    UpdateTagBody,
    _OktaGroupRequestBody,
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
        CreateTagBody.model_validate({"name": "tag", "constraints": {"disallow_self_add_ownership": "not-a-bool"}})


def test_create_tag_body_accepts_known_constraint() -> None:
    body = CreateTagBody.model_validate({"name": "tag", "constraints": {"disallow_self_add_ownership": True}})
    assert body.constraints == {"disallow_self_add_ownership": True}


def test_update_tag_body_partial_skips_description_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REQUIRE_DESCRIPTIONS", True)
    UpdateTagBody.model_validate({"enabled": False})


def test_update_tag_body_unknown_constraint_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateTagBody.model_validate({"constraints": {"bogus": 1}})


# --- Reason normalization ---------------------------------------------------
#
# `CheckForReason.invalid_reason` strips before deciding whether a reason was
# given. Normalizing at the boundary is what makes that gate and the stored
# value agree: without it, "provided" means one thing on a group whose tag
# requires a reason and another on a group that does not, and blank-looking
# text reaches the audit trail either way. It also covers the `request_reason`
# fields, which no constraint consults.


@pytest.mark.parametrize(
    ("model", "field", "extra"),
    [
        (CreateAccessRequestBody, "reason", {"group_id": "a" * 20, "group_owner": False}),
        (ResolveAccessRequestBody, "reason", {"approved": True}),
        (ResolveRoleRequestBody, "reason", {"approved": True}),
        (ResolveGroupRequestBody, "reason", {"approved": True}),
        (
            CreateRoleRequestBody,
            "reason",
            {"group_id": "a" * 20, "role_id": "b" * 20, "group_owner": False},
        ),
        (
            GroupMember,
            "created_reason",
            {
                "members_to_add": [],
                "members_to_remove": [],
                "owners_to_add": [],
                "owners_to_remove": [],
            },
        ),
        (
            RoleMember,
            "created_reason",
            {
                "groups_to_add": [],
                "groups_to_remove": [],
                "owner_groups_to_add": [],
                "owner_groups_to_remove": [],
            },
        ),
    ],
)
def test_whitespace_only_reason_normalizes_to_empty(model: Any, field: str, extra: dict[str, Any]) -> None:
    body = model.model_validate({**extra, field: "   \t\n "})
    assert getattr(body, field) == ""


@pytest.mark.parametrize(
    ("model", "field", "extra"),
    [
        (ResolveAccessRequestBody, "reason", {"approved": True}),
        (
            GroupMember,
            "created_reason",
            {
                "members_to_add": [],
                "members_to_remove": [],
                "owners_to_add": [],
                "owners_to_remove": [],
            },
        ),
    ],
)
def test_surrounding_whitespace_is_trimmed_from_a_real_reason(model: Any, field: str, extra: dict[str, Any]) -> None:
    body = model.model_validate({**extra, field: "  needs ledger export  "})
    assert getattr(body, field) == "needs ledger export"


def test_group_request_body_normalizes_its_request_reason() -> None:
    """`request_reason` never reaches `CheckForReason`, so the boundary is the
    only thing standing between whitespace and the stored record."""
    body = _OktaGroupRequestBody.model_validate(
        {
            "requested_group_name": "Some-Group",
            "requested_group_description": "d",
            "requested_group_type": "okta_group",
            "request_reason": "   ",
        }
    )
    assert body.request_reason == ""
