import json
import logging
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from starlette.requests import Request

from api.auth.dependencies import get_current_user_id
from api.auth.permissions import require_access_admin_or_app_creator
from api.config import settings
from api.extensions import Db
from api.models import OktaUser, RoleGroup, ServicePrincipal, ServicePrincipalOwner, ServicePrincipalProvider
from api.operations import CreateServicePrincipal, DeleteServicePrincipal, ModifyServicePrincipal
from tests.factories import OktaUserFactory, RoleGroupFactory, ServicePrincipalFactory

SP_URL = "/api/service-principals"


async def _create_owned_principal(db: Db, owner: OktaUser, **kwargs: Any) -> ServicePrincipal:
    sp = ServicePrincipalFactory.build(**kwargs)
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    return sp


async def test_create_service_principal(client: AsyncClient, db: Db, caplog: pytest.LogCaptureFixture) -> None:
    owner = await OktaUserFactory.create_async()
    role = await RoleGroupFactory.create_async()

    with caplog.at_level(logging.INFO, logger="access.audit"):
        rep = await client.post(
            SP_URL,
            json={
                "display_name": "Deploy bot",
                "subject": "abc123.access",
                "owner_user_ids": [owner.id],
                "owner_role_ids": [role.id],
            },
        )
    assert rep.status_code == 201, rep.text
    data = rep.json()
    assert data["id"].startswith(ServicePrincipal.ID_PREFIX)
    assert data["display_name"] == "Deploy bot"
    assert data["provider"] == "cloudflare"
    assert data["subject"] == "abc123.access"
    assert {o["user_id"] for o in data["owners"]} == {owner.id, None}
    assert {o["role_group_id"] for o in data["owners"]} == {role.id, None}
    assert next(o for o in data["owners"] if o["user_id"])["active_user"]["email"] == owner.email
    assert next(o for o in data["owners"] if o["role_group_id"])["active_role_group"]["name"] == role.name

    audit = [json.loads(r.getMessage()) for r in caplog.records if r.name == "access.audit"]
    assert audit[-1]["event_type"] == "SERVICE_PRINCIPAL_CREATE"
    assert audit[-1]["service_principal"]["id"] == data["id"]
    assert audit[-1]["service_principal"]["owner_user_ids"] == [owner.id]
    assert audit[-1]["service_principal"]["owner_role_ids"] == [role.id]


async def test_create_service_principal_validation(client: AsyncClient, db: Db) -> None:
    rep = await client.post(SP_URL, json={"display_name": "bot", "subject": "s"})
    assert rep.status_code == 400

    rep = await client.post(SP_URL, json={"display_name": "bot", "subject": "s", "owner_user_ids": ["nope"]})
    assert rep.status_code == 400
    assert "nope" in rep.json()["detail"]

    deleted_role = await RoleGroupFactory.create_async()
    deleted_role.deleted_at = deleted_role.created_at
    await db.session.commit()
    rep = await client.post(SP_URL, json={"display_name": "bot", "subject": "s", "owner_role_ids": [deleted_role.id]})
    assert rep.status_code == 400


async def test_create_service_principal_duplicate_subject(client: AsyncClient, db: Db) -> None:
    owner = await OktaUserFactory.create_async()
    body = {"display_name": "bot", "subject": "dup.access", "owner_user_ids": [owner.id]}

    first = await client.post(SP_URL, json=body)
    assert first.status_code == 201
    rep = await client.post(SP_URL, json=body)
    assert rep.status_code == 409

    # A deleted principal frees its subject for re-registration.
    assert (await client.delete(f"{SP_URL}/{first.json()['id']}")).status_code == 200
    rep = await client.post(SP_URL, json=body)
    assert rep.status_code == 201
    assert rep.json()["id"] != first.json()["id"]


async def test_service_principal_writes_require_admin(client: AsyncClient, db: Db, mock_user: Any) -> None:
    owner = await OktaUserFactory.create_async()
    sp = await _create_owned_principal(db, owner)
    url = f"{SP_URL}/{sp.id}"
    owner_id = owner.id
    mock_user(owner)

    rep = await client.post(SP_URL, json={"display_name": "bot", "subject": "x", "owner_user_ids": [owner_id]})
    assert rep.status_code == 403
    rep = await client.put(url, json={"display_name": "renamed"})
    assert rep.status_code == 403
    rep = await client.delete(url)
    assert rep.status_code == 403


async def test_service_principal_reads_open_to_authenticated_users(client: AsyncClient, db: Db, mock_user: Any) -> None:
    owner = await OktaUserFactory.create_async()
    sp = await _create_owned_principal(db, owner, display_name="Searchable bot")
    other = await _create_owned_principal(db, owner, display_name="Other")
    mock_user(owner)

    subject = sp.subject
    rep = await client.get(f"{SP_URL}/{sp.id}")
    assert rep.status_code == 200
    assert rep.json()["display_name"] == "Searchable bot"
    # The credential's subject is for Access admins only, and can't be searched for.
    assert rep.json()["subject"] is None
    assert (await client.get(SP_URL, params={"q": subject})).json()["items"] == []

    rep = await client.get(SP_URL)
    assert rep.status_code == 200
    assert {i["id"] for i in rep.json()["items"]} == {sp.id, other.id}
    assert all(i["subject"] is None for i in rep.json()["items"])

    rep = await client.get(SP_URL, params={"q": "searchable"})
    assert [i["id"] for i in rep.json()["items"]] == [sp.id]

    assert (await client.get(f"{SP_URL}/sp_missing")).status_code == 404


async def test_update_service_principal(client: AsyncClient, db: Db, caplog: pytest.LogCaptureFixture) -> None:
    owner = await OktaUserFactory.create_async()
    role: RoleGroup = await RoleGroupFactory.create_async()
    sp = await _create_owned_principal(db, owner)
    taken = await _create_owned_principal(db, owner)
    url = f"{SP_URL}/{sp.id}"
    original_subject, taken_subject, owner_id, role_id = sp.subject, taken.subject, owner.id, role.id

    with caplog.at_level(logging.INFO, logger="access.audit"):
        rep = await client.put(url, json={"display_name": "Renamed", "subject": "rotated.access"})
    assert rep.status_code == 200, rep.text
    assert rep.json()["display_name"] == "Renamed"
    assert rep.json()["subject"] == "rotated.access"
    assert [o["user_id"] for o in rep.json()["owners"]] == [owner_id]
    audit = [json.loads(r.getMessage()) for r in caplog.records if r.name == "access.audit"]
    assert audit[-1]["event_type"] == "SERVICE_PRINCIPAL_MODIFY"
    assert audit[-1]["old_service_principal"]["subject"] == original_subject
    assert audit[-1]["service_principal"]["subject"] == "rotated.access"

    # Sending only role owners replaces the role owners and keeps the user owners.
    rep = await client.put(url, json={"owner_role_ids": [role_id]})
    assert rep.status_code == 200
    assert {(o["user_id"], o["role_group_id"]) for o in rep.json()["owners"]} == {(owner_id, None), (None, role_id)}

    rep = await client.put(url, json={"owner_user_ids": []})
    assert rep.status_code == 200
    assert [o["role_group_id"] for o in rep.json()["owners"]] == [role_id]

    rep = await client.put(url, json={"owner_role_ids": []})
    assert rep.status_code == 400

    rep = await client.put(url, json={"subject": taken_subject})
    assert rep.status_code == 409

    assert (await client.put(f"{SP_URL}/sp_missing", json={})).status_code == 404


async def test_delete_service_principal(client: AsyncClient, db: Db, caplog: pytest.LogCaptureFixture) -> None:
    owner = await OktaUserFactory.create_async()
    sp = await _create_owned_principal(db, owner)
    sp_id = sp.id
    url = f"{SP_URL}/{sp_id}"

    with caplog.at_level(logging.INFO, logger="access.audit"):
        rep = await client.delete(url)
    assert rep.status_code == 200
    audit = [json.loads(r.getMessage()) for r in caplog.records if r.name == "access.audit"]
    assert audit[-1]["event_type"] == "SERVICE_PRINCIPAL_DELETE"
    assert audit[-1]["service_principal"]["id"] == sp_id

    assert (await client.get(url)).status_code == 404
    assert (await client.delete(url)).status_code == 404
    assert (await client.get(SP_URL)).json()["items"] == []


async def test_deleted_owner_is_hidden(client: AsyncClient, db: Db) -> None:
    owner = await OktaUserFactory.create_async()
    sp = await _create_owned_principal(db, owner)
    owner.deleted_at = owner.created_at
    await db.session.commit()

    rep = await client.get(f"{SP_URL}/{sp.id}")
    assert rep.status_code == 200
    assert rep.json()["owners"][0]["user_id"] == owner.id
    assert rep.json()["owners"][0]["active_user"] is None


# --- Authentication ---------------------------------------------------------


@pytest.fixture
def cloudflare_payload(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Route `get_current_user_id` through the Cloudflare branch with a stubbed, verified JWT payload."""
    payload: dict[str, Any] = {}
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "CLOUDFLARE_TEAM_DOMAIN", "team.cloudflareaccess.com")
    monkeypatch.setattr("api.auth.dependencies.extract_token", lambda request: "token")
    monkeypatch.setattr("api.auth.dependencies.verify_cloudflare_token", lambda token: payload)
    return payload


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


async def test_registered_service_token_authenticates_as_principal(db: Db, cloudflare_payload: dict[str, Any]) -> None:
    owner = await OktaUserFactory.create_async()
    sp = await _create_owned_principal(db, owner)
    cloudflare_payload["common_name"] = sp.subject

    request = _request()
    assert await get_current_user_id(request, db.session) == sp.id
    assert request.state.current_user_id == sp.id


async def test_unregistered_service_token_passes_through(db: Db, cloudflare_payload: dict[str, Any]) -> None:
    cloudflare_payload["common_name"] = "unregistered.access"
    assert await get_current_user_id(_request(), db.session) == "unregistered.access"


async def test_retired_service_token_is_refused(
    db: Db, cloudflare_payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await OktaUserFactory.create_async()

    async def register(subject: str) -> str:
        sp = await CreateServicePrincipal(
            display_name=subject,
            provider=ServicePrincipalProvider.CLOUDFLARE,
            subject=subject,
            owner_user_ids=[owner.id],
            owner_role_ids=[],
        ).execute()
        return sp.id

    deleted = await register("deleted.access")
    rotated = await register("old.access")
    await DeleteServicePrincipal(service_principal=deleted).execute()
    await ModifyServicePrincipal(service_principal=rotated, subject="new.access").execute()
    # Still in an allowlist, but registration ended that access for good.
    monkeypatch.setattr(settings, "APP_CREATOR_ID", "deleted.access,old.access")

    for subject in ("deleted.access", "old.access"):
        cloudflare_payload["common_name"] = subject
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(_request(), db.session)
        assert exc.value.status_code == 403
    cloudflare_payload["common_name"] = "new.access"
    assert await get_current_user_id(_request(), db.session) == rotated


async def test_concurrent_subject_registration_conflicts(
    client: AsyncClient, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await OktaUserFactory.create_async()
    body = {"display_name": "bot", "subject": "race.access", "owner_user_ids": [owner.id]}
    assert (await client.post(SP_URL, json=body)).status_code == 201

    async def no_check(*args: Any, **kwargs: Any) -> None:
        return None

    # Lose the race: the pre-check passes, and the unique index refuses the insert.
    monkeypatch.setattr("api.operations.create_service_principal.ensure_subject_available", no_check)
    rep = await client.post(SP_URL, json=body)
    assert rep.status_code == 409


async def test_registering_a_token_ends_its_allowlist_access(
    db: Db, cloudflare_payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await OktaUserFactory.create_async()
    registered = await _create_owned_principal(db, owner)
    monkeypatch.setattr(settings, "APP_CREATOR_ID", f"{registered.subject},unregistered.access")

    cloudflare_payload["common_name"] = "unregistered.access"
    caller = await get_current_user_id(_request(), db.session)
    assert await require_access_admin_or_app_creator(db.session, caller) == caller

    cloudflare_payload["common_name"] = registered.subject
    caller = await get_current_user_id(_request(), db.session)
    with pytest.raises(HTTPException) as exc:
        await require_access_admin_or_app_creator(db.session, caller)
    assert exc.value.status_code == 403
