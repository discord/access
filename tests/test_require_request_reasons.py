"""`REQUIRE_REQUEST_REASONS` makes the requester's reason mandatory on every
new access, role, and group request, independent of the target's tags."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import AccessRequest, AccessRequestStatus, OktaGroup, RoleGroup
from api.schemas.requests_schemas import (
    CreateAccessRequestBody,
    CreateRoleRequestBody,
    _OktaGroupRequestBody,
)

# Each body with its minimal valid payload and the name of its reason field.
_BODIES: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (CreateAccessRequestBody, {"group_id": "g"}, "reason"),
    (CreateRoleRequestBody, {"role_id": "r", "group_id": "g"}, "reason"),
    (
        _OktaGroupRequestBody,
        {"requested_group_name": "Foo", "requested_group_description": "x", "requested_group_type": "okta_group"},
        "request_reason",
    ),
]


@pytest.mark.parametrize(("body", "payload", "field"), _BODIES)
def test_reason_optional_when_setting_off(body: type[BaseModel], payload: dict[str, Any], field: str) -> None:
    assert getattr(body.model_validate(payload), field) == ""


@pytest.mark.parametrize(("body", "payload", "field"), _BODIES)
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_blank_reason_rejected_when_required(
    monkeypatch: pytest.MonkeyPatch, body: type[BaseModel], payload: dict[str, Any], field: str, reason: Any
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_REQUEST_REASONS", True)
    with pytest.raises(ValidationError, match="A reason is required"):
        body.model_validate({**payload, field: reason})


@pytest.mark.parametrize(("body", "payload", "field"), _BODIES)
def test_omitted_reason_rejected_when_required(
    monkeypatch: pytest.MonkeyPatch, body: type[BaseModel], payload: dict[str, Any], field: str
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_REQUEST_REASONS", True)
    with pytest.raises(ValidationError, match="A reason is required"):
        body.model_validate(payload)


@pytest.mark.parametrize(("body", "payload", "field"), _BODIES)
def test_reason_accepted_and_stripped_when_required(
    monkeypatch: pytest.MonkeyPatch, body: type[BaseModel], payload: dict[str, Any], field: str
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_REQUEST_REASONS", True)
    assert getattr(body.model_validate({**payload, field: "  need it  "}), field) == "need it"


async def test_access_request_requires_reason_via_http(
    client: AsyncClient, db: Db, okta_group: OktaGroup, url_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.session.add(okta_group)
    await db.session.commit()
    url = url_for("api-access-requests.access_requests")

    # Submitted while the setting is off, so it exists to be superseded.
    rep = await client.post(url, json={"group_id": okta_group.id})
    assert rep.status_code == 201
    prior_id = rep.json()["id"]

    monkeypatch.setattr(settings, "REQUIRE_REQUEST_REASONS", True)

    rep = await client.post(url, json={"group_id": okta_group.id, "reason": " "})
    assert rep.status_code == 400
    assert "A reason is required" in rep.text
    # The rejected resubmission must not have superseded the pending request.
    prior = (await db.session.scalars(select(AccessRequest).where(AccessRequest.id == prior_id))).one()
    assert prior.status == AccessRequestStatus.PENDING

    rep = await client.post(url, json={"group_id": okta_group.id, "reason": "on-call rotation"})
    assert rep.status_code == 201
    assert rep.json()["request_reason"] == "on-call rotation"


async def test_role_request_requires_reason_via_http(
    client: AsyncClient,
    db: Db,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    url_for: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.session.add_all([role_group, okta_group])
    await db.session.commit()
    monkeypatch.setattr(settings, "REQUIRE_REQUEST_REASONS", True)
    url = url_for("api-role-requests.role_requests")
    payload = {"role_id": role_group.id, "group_id": okta_group.id}

    rep = await client.post(url, json=payload)
    assert rep.status_code == 400
    assert "A reason is required" in rep.text

    rep = await client.post(url, json={**payload, "reason": "team needs it"})
    assert rep.status_code == 201


async def test_group_request_requires_reason_via_http(
    client: AsyncClient, db: Db, url_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_REQUEST_REASONS", True)
    url = url_for("api-group-requests.group_requests_create")
    payload = {
        "requested_group_name": "Requested-Group",
        "requested_group_description": "x",
        "requested_group_type": "okta_group",
    }

    rep = await client.post(url, json=payload)
    assert rep.status_code == 400
    assert "A reason is required" in rep.text

    rep = await client.post(url, json={**payload, "request_reason": "new project"})
    assert rep.status_code == 201
