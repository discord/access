"""Tests for the Sentry envelope tunnel at POST /api/bugs/sentry. The
endpoint is a *passthrough* to Sentry, so verify the rewrite + forward
behavior end-to-end without making a real outbound request — H8 in the
FastAPI migration plan."""

from __future__ import annotations

from typing import Any, Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.auth.dependencies import require_authenticated
from api.config import settings


_REAL_DSN = "https://abc@o12345.ingest.sentry.io/777"
_PLACEHOLDER_DSN = "https://user@example.ingest.sentry.io/1234567"
_ENVELOPE = (
    f'{{"event_id":"abc","dsn":"{_PLACEHOLDER_DSN}"}}\n'
    '{{"type":"event"}}\n'
    '{{"message":"hi"}}\n'
)


@pytest.fixture
def production_env(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Bypass the dev/test auth shortcut so the 'production' branch of
    `bugs.post_sentry` runs. `require_authenticated` is overridden to no-op
    because the real path would try to verify a Cloudflare/OIDC token."""
    monkeypatch.setattr(settings, "ENV", "production")
    app.dependency_overrides[require_authenticated] = lambda: None
    yield
    app.dependency_overrides.pop(require_authenticated, None)


@pytest.fixture
def with_real_dsn(production_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REACT_SENTRY_DSN", _REAL_DSN)


def test_sentry_envelope_is_forwarded_with_dsn_rewritten(
    client: TestClient, with_real_dsn: None, mocker: MockerFixture
) -> None:
    captured: dict[str, Any] = {}

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, *, content: str, headers: dict[str, str], timeout: float) -> None:
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers

    mocker.patch("api.routers.bugs.httpx.AsyncClient", _FakeAsyncClient)

    rep = client.post("/api/bugs/sentry", content=_ENVELOPE, headers={"Content-Type": "application/x-sentry-envelope"})
    assert rep.status_code == 200
    assert rep.json() == {}

    assert captured, "envelope was not forwarded"
    assert captured["url"] == "https://o12345.ingest.sentry.io/api/777/envelope/"
    assert _PLACEHOLDER_DSN not in captured["content"]
    assert _REAL_DSN in captured["content"]
    assert captured["headers"]["Content-Type"] == "application/x-sentry-envelope"


def test_sentry_skips_in_dev(client: TestClient, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    # Default test env is "test"; override DSN but keep ENV → expect skip.
    monkeypatch.setattr(settings, "REACT_SENTRY_DSN", _REAL_DSN)
    spy = mocker.patch("api.routers.bugs.httpx.AsyncClient")

    rep = client.post("/api/bugs/sentry", content=_ENVELOPE)
    assert rep.status_code == 200
    spy.assert_not_called()


def test_sentry_skips_when_dsn_unset(
    client: TestClient, production_env: None, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    monkeypatch.setattr(settings, "REACT_SENTRY_DSN", None)
    spy = mocker.patch("api.routers.bugs.httpx.AsyncClient")

    rep = client.post("/api/bugs/sentry", content=_ENVELOPE)
    assert rep.status_code == 200
    spy.assert_not_called()
