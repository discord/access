"""End-to-end tests for the OIDC login flow.

The conftest `client` fixture exercises the dev/test auth bypass branch in
`get_current_user_id`. To reach the OIDC branch we build a fresh
OIDC-configured app per test with `settings.ENV` set outside
`{"development", "test"}` and Authlib mocked at the `oauth.oidc` boundary.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Generator

import itsdangerous
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from api import app as app_module
from api.app import create_app
from api.auth import oidc as oidc_module
from api.config import settings
from api.extensions import Db
from api.models import OktaUser
from tests.factories import OktaUserFactory

TEST_SECRET_KEY = "test-oidc-secret-key-min-32-bytes-long!!"
TEST_OIDC_USER_EMAIL = "oidc-user@example.com"
STUB_OIDC_CLIENT_SECRETS = {
    "web": {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "issuer": "https://idp.test",
    }
}


def _signed_session_cookie(secret_key: str, data: dict[str, Any]) -> str:
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    return itsdangerous.TimestampSigner(secret_key).sign(payload).decode("utf-8")


def _decode_session_cookie(secret_key: str, cookie_value: str) -> dict[str, Any]:
    signer = itsdangerous.TimestampSigner(secret_key)
    raw = signer.unsign(cookie_value)
    return json.loads(base64.b64decode(raw).decode("utf-8"))


def _build_oidc_mock(
    userinfo: dict[str, Any] | None = None,
    server_metadata: dict[str, Any] | None = None,
) -> SimpleNamespace:
    info = userinfo if userinfo is not None else {"email": TEST_OIDC_USER_EMAIL}
    metadata = server_metadata if server_metadata is not None else {}

    async def authorize_redirect(request: Any, redirect_uri: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"https://idp.test/authorize?redirect_uri={redirect_uri}",
            status_code=302,
        )

    async def authorize_access_token(request: Any) -> dict[str, Any]:
        return {"userinfo": info}

    async def userinfo_(token: Any = None) -> dict[str, Any]:
        return info

    async def load_server_metadata() -> dict[str, Any]:
        return metadata

    return SimpleNamespace(
        authorize_redirect=authorize_redirect,
        authorize_access_token=authorize_access_token,
        userinfo=userinfo_,
        load_server_metadata=load_server_metadata,
    )


@pytest.fixture
def seed_oidc_user(db: Db) -> OktaUser:
    user = OktaUserFactory.build(email=TEST_OIDC_USER_EMAIL)
    db.session.add(user)
    db.session.commit()
    return user


def _install_oidc_settings(env: str) -> None:
    settings.ENV = env
    settings.SECRET_KEY = TEST_SECRET_KEY
    settings.OIDC_CLIENT_SECRETS = STUB_OIDC_CLIENT_SECRETS
    settings.CLOUDFLARE_TEAM_DOMAIN = None
    # Suppress create_app's `db.init_app(build_engine())` branch — the `db`
    # fixture has already bound an in-memory engine with the seeded schema,
    # and rebinding here would point queries at an empty DB.
    settings.SQLALCHEMY_DATABASE_URI = None
    settings.CLOUDSQL_CONNECTION_NAME = None


@pytest.fixture
def oidc_mock() -> SimpleNamespace:
    return _build_oidc_mock()


@pytest.fixture
def stub_build_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    # CI doesn't run the Vite build, so `api/app.py:BUILD_DIR` doesn't exist
    # and the SPA catch-all route never registers. The routing tests rely on
    # that catch-all going through `require_authenticated`, so point
    # BUILD_DIR at a stub directory with an index.html before create_app.
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html><body>stub</body></html>")
    monkeypatch.setattr(app_module, "BUILD_DIR", build_dir)
    return build_dir


def _install_oidc_mock(mock: SimpleNamespace) -> None:
    # Authlib's `__getattr__("oidc")` consults `_registry` and routes
    # through `create_client`, which returns `_clients[name]` if present.
    # Populate both so `oauth.oidc` resolves to the mock without ever
    # invoking the real Authlib client constructor or network metadata
    # discovery, and so `register_oidc`'s `"oidc" not in _clients` guard
    # short-circuits the real `oauth.register()` call.
    oidc_module.oauth._clients["oidc"] = mock
    oidc_module.oauth._registry["oidc"] = (False, {})


def _restore_oidc_registry(saved_clients: dict[str, Any], saved_registry: dict[str, Any]) -> None:
    oidc_module.oauth._clients.clear()
    oidc_module.oauth._clients.update(saved_clients)
    oidc_module.oauth._registry.clear()
    oidc_module.oauth._registry.update(saved_registry)


@pytest.fixture
def oidc_app(
    monkeypatch: pytest.MonkeyPatch,
    db: Db,
    seed_oidc_user: OktaUser,
    oidc_mock: SimpleNamespace,
    stub_build_dir: Path,
) -> Generator[FastAPI, None, None]:
    saved = (
        settings.ENV,
        settings.SECRET_KEY,
        settings.OIDC_CLIENT_SECRETS,
        settings.CLOUDFLARE_TEAM_DOMAIN,
        settings.SQLALCHEMY_DATABASE_URI,
        settings.CLOUDSQL_CONNECTION_NAME,
    )
    saved_clients = dict(oidc_module.oauth._clients)
    saved_registry = dict(oidc_module.oauth._registry)
    monkeypatch.setenv("ENV", "staging")
    try:
        _install_oidc_settings("staging")
        _install_oidc_mock(oidc_mock)
        app = create_app(testing=False)
        yield app
    finally:
        (
            settings.ENV,
            settings.SECRET_KEY,
            settings.OIDC_CLIENT_SECRETS,
            settings.CLOUDFLARE_TEAM_DOMAIN,
            settings.SQLALCHEMY_DATABASE_URI,
            settings.CLOUDSQL_CONNECTION_NAME,
        ) = saved
        _restore_oidc_registry(saved_clients, saved_registry)


@pytest.fixture
def oidc_client(oidc_app: FastAPI) -> Generator[TestClient, None, None]:
    # Use https so the cookie jar will replay a Secure-flagged session cookie
    # back to the server on subsequent requests.
    with TestClient(
        oidc_app,
        base_url="https://testserver",
        raise_server_exceptions=False,
        follow_redirects=False,
    ) as c:
        yield c


@pytest.fixture
def dev_oidc_app(
    monkeypatch: pytest.MonkeyPatch,
    db: Db,
    seed_oidc_user: OktaUser,
    oidc_mock: SimpleNamespace,
    stub_build_dir: Path,
) -> Generator[FastAPI, None, None]:
    saved = (
        settings.ENV,
        settings.SECRET_KEY,
        settings.OIDC_CLIENT_SECRETS,
        settings.CLOUDFLARE_TEAM_DOMAIN,
        settings.SQLALCHEMY_DATABASE_URI,
        settings.CLOUDSQL_CONNECTION_NAME,
    )
    saved_clients = dict(oidc_module.oauth._clients)
    saved_registry = dict(oidc_module.oauth._registry)
    monkeypatch.setenv("ENV", "development")
    try:
        _install_oidc_settings("development")
        _install_oidc_mock(oidc_mock)
        app = create_app(testing=False)
        yield app
    finally:
        (
            settings.ENV,
            settings.SECRET_KEY,
            settings.OIDC_CLIENT_SECRETS,
            settings.CLOUDFLARE_TEAM_DOMAIN,
            settings.SQLALCHEMY_DATABASE_URI,
            settings.CLOUDSQL_CONNECTION_NAME,
        ) = saved
        _restore_oidc_registry(saved_clients, saved_registry)


@pytest.fixture
def dev_oidc_client(dev_oidc_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(dev_oidc_app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


def _set_session(client: TestClient, data: dict[str, Any]) -> None:
    client.cookies.set("session", _signed_session_cookie(TEST_SECRET_KEY, data))


def _read_session_from_set_cookie(set_cookie_header: str) -> dict[str, Any] | None:
    if not set_cookie_header:
        return None
    cookie_value = set_cookie_header.split(";", 1)[0].split("=", 1)[1]
    if cookie_value in ("null", ""):
        return None
    return _decode_session_cookie(TEST_SECRET_KEY, cookie_value)


# ---------------------------------------------------------------------------
# Routing and auth gate
# ---------------------------------------------------------------------------


def test_unauthenticated_protected_endpoint_redirects_to_oidc_login(
    oidc_client: TestClient,
) -> None:
    response = oidc_client.get("/api/users")
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("/oidc/login?next=")
    assert "next=%2Fapi%2Fusers" in location


def test_oidc_login_is_in_allowlist(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/login")
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://idp.test/authorize")


def test_oidc_authorize_is_in_allowlist(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/authorize")
    assert response.status_code in (302, 307)
    assert not response.headers["location"].startswith("/oidc/login")


def test_oidc_logout_is_in_allowlist(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/logout")
    assert response.status_code in (302, 307)
    assert not response.headers["location"].startswith("/oidc/login")


def test_unmapped_api_path_redirects_through_auth_gate(oidc_client: TestClient) -> None:
    response = oidc_client.get("/api/this-endpoint-does-not-exist")
    assert response.status_code == 307
    assert response.headers["location"].startswith("/oidc/login?next=")


def test_unauthenticated_spa_path_redirects_to_oidc_login(oidc_client: TestClient) -> None:
    response = oidc_client.get("/groups/foo")
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("/oidc/login?next=")
    assert "next=%2Fgroups%2Ffoo" in location


# ---------------------------------------------------------------------------
# `next` parameter validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "next_value",
    ["/dashboard", "/groups/foo", "/api/users/me", "/"],
)
def test_oidc_login_stores_safe_next(oidc_client: TestClient, next_value: str) -> None:
    response = oidc_client.get(f"/oidc/login?next={next_value}")
    assert response.status_code == 302
    session = _read_session_from_set_cookie(response.headers.get("set-cookie", ""))
    assert session is not None
    assert session.get("oidc_next") == next_value


@pytest.mark.parametrize(
    "next_value",
    [
        "https://evil.example.com",
        "https://evil.example.com/x",
        "//evil.example.com",
        "//evil.example.com/x",
        "/\\evil.example.com",
        "http://localhost/x",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "",
        "relative/path",
    ],
)
def test_oidc_login_drops_unsafe_next(oidc_client: TestClient, next_value: str) -> None:
    response = oidc_client.get("/oidc/login", params={"next": next_value})
    assert response.status_code == 302
    session = _read_session_from_set_cookie(response.headers.get("set-cookie", ""))
    if session is not None:
        assert "oidc_next" not in session


def test_oidc_authorize_uses_stored_next(oidc_client: TestClient) -> None:
    _set_session(oidc_client, {"oidc_next": "/groups"})
    response = oidc_client.get("/oidc/authorize")
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/groups"
    session = _read_session_from_set_cookie(response.headers.get("set-cookie", ""))
    assert session is not None
    assert "oidc_next" not in session
    assert session.get("userinfo", {}).get("email") == TEST_OIDC_USER_EMAIL


def test_oidc_authorize_defaults_to_root_when_no_next(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/authorize")
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Happy-path login
# ---------------------------------------------------------------------------


def test_full_login_flow(oidc_client: TestClient, seed_oidc_user: OktaUser) -> None:
    initial = oidc_client.get("/api/users")
    assert initial.status_code == 307
    redirect_target = initial.headers["location"]
    assert redirect_target.startswith("/oidc/login?next=")

    login = oidc_client.get(redirect_target)
    assert login.status_code == 302
    assert login.headers["location"].startswith("https://idp.test/authorize")

    callback = oidc_client.get("/oidc/authorize")
    assert callback.status_code in (302, 307)
    assert callback.headers["location"] == "/api/users"

    final = oidc_client.get("/api/users")
    assert final.status_code == 200


def test_login_with_existing_session_still_bounces_through_idp(
    oidc_client: TestClient,
) -> None:
    _set_session(oidc_client, {"userinfo": {"email": TEST_OIDC_USER_EMAIL}})
    response = oidc_client.get("/oidc/login?next=/dashboard")
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://idp.test/authorize")


# ---------------------------------------------------------------------------
# Authorize error paths
# ---------------------------------------------------------------------------


def test_authorize_token_exchange_failure_returns_403(oidc_client: TestClient, oidc_mock: SimpleNamespace) -> None:
    async def boom(request: Any) -> dict[str, Any]:
        raise RuntimeError("upstream IdP rejected the code")

    oidc_mock.authorize_access_token = boom
    response = oidc_client.get("/oidc/authorize")
    assert response.status_code == 403
    assert "OIDC authorization failed" in response.text


def test_authorize_unknown_email_returns_404_on_next_request(
    oidc_client: TestClient, oidc_mock: SimpleNamespace
) -> None:
    async def unknown_user(request: Any) -> dict[str, Any]:
        return {"userinfo": {"email": "stranger@example.com"}}

    oidc_mock.authorize_access_token = unknown_user
    callback = oidc_client.get("/oidc/authorize")
    assert callback.status_code in (302, 307)

    follow_up = oidc_client.get("/api/users")
    assert follow_up.status_code == 404


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_clears_session_and_redirects_home(oidc_client: TestClient) -> None:
    _set_session(oidc_client, {"userinfo": {"email": TEST_OIDC_USER_EMAIL}})
    response = oidc_client.get("/oidc/logout")
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/"
    set_cookie = response.headers.get("set-cookie", "")
    if "session=" in set_cookie:
        session = _read_session_from_set_cookie(set_cookie)
        assert session is None or "userinfo" not in session


def test_logout_does_not_call_idp_end_session(oidc_client: TestClient, oidc_mock: SimpleNamespace) -> None:
    # Even if the IdP advertises an `end_session_endpoint`, /oidc/logout only
    # clears the local session. Driving the IdP-side logout would require an
    # `id_token_hint` (Okta returns 400 without one) and a registered
    # `post_logout_redirect_uri`, neither of which we wire up.
    metadata_calls = 0

    async def metadata_with_end_session() -> dict[str, Any]:
        nonlocal metadata_calls
        metadata_calls += 1
        return {"end_session_endpoint": "https://idp.test/v1/logout"}

    oidc_mock.load_server_metadata = metadata_with_end_session
    _set_session(oidc_client, {"userinfo": {"email": TEST_OIDC_USER_EMAIL}})
    response = oidc_client.get("/oidc/logout")
    assert response.headers["location"] == "/"
    assert metadata_calls == 0


def test_post_logout_request_redirects_back_to_login(oidc_client: TestClient) -> None:
    _set_session(oidc_client, {"userinfo": {"email": TEST_OIDC_USER_EMAIL}})
    logout = oidc_client.get("/oidc/logout")
    assert logout.status_code in (302, 307)

    oidc_client.cookies.clear()
    response = oidc_client.get("/api/users")
    assert response.status_code == 307
    assert response.headers["location"].startswith("/oidc/login?next=")


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_hsts_present_in_non_dev(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/login")
    assert response.headers.get("Strict-Transport-Security") == ("max-age=31536000; includeSubDomains")


def test_hsts_absent_in_dev(dev_oidc_client: TestClient) -> None:
    response = dev_oidc_client.get("/oidc/login")
    assert "Strict-Transport-Security" not in response.headers


def test_other_security_headers_still_set(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/login")
    assert "Content-Security-Policy" in response.headers
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "no-referrer"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


# ---------------------------------------------------------------------------
# Session cookie flags
# ---------------------------------------------------------------------------


def test_session_cookie_flags_in_non_dev(oidc_client: TestClient) -> None:
    response = oidc_client.get("/oidc/login?next=/dashboard")
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "session=" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie


def test_session_cookie_flags_in_dev_omit_secure(dev_oidc_client: TestClient) -> None:
    dev_oidc_client.cookies.set("session", _signed_session_cookie(TEST_SECRET_KEY, {"oidc_next": "/x"}))
    response = dev_oidc_client.get("/oidc/logout")
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie
