"""Tests for the embedded MCP server.

Covers the seams that aren't exercised by the REST test suite:

  - Provider-chain semantics: the middleware tries dev → cloudflare →
    oidc, and the first non-None ``MCPIdentity`` wins.
  - ``MCPAuthMiddleware`` 401 path when every provider defers.
  - ``require_scope`` enforcement on a representative read tool and on
    the only v1 write tool.
  - ``source="mcp"`` propagation into the ``RequestContext`` /
    ``AuditLogSchema`` payload during a write.

The MCP route is mounted as a Starlette ``Route`` whose handler is the
FastMCP ``StreamableHTTPASGIApp``. Round-tripping a full Streamable HTTP
JSON-RPC session against an in-memory TestClient is fragile across MCP
SDK versions, so most tool-level tests call the registered tool
function directly with an ``MCPIdentity`` bound on the context — the
exact code path FastMCP would take, minus the JSON-RPC framing.
"""

from __future__ import annotations

import json
from typing import Any, Generator, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.config import settings
from api.context import RequestContext, set_request_context
from api.extensions import Db
from api.mcp.auth import (
    ALL_V1_SCOPES,
    MCP_SCOPE_CREATE_REQUESTS,
    MCP_SCOPE_READ_ALL,
    MCPIdentity,
    set_mcp_identity,
)
from api.models import App, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations import ModifyGroupUsers
from tests.factories import RoleGroupFactory


@pytest.fixture
def with_mcp_enabled() -> Generator[None, None, None]:
    """Flip ENABLE_MCP for the test, clearing the cached server singleton
    on both sides so create_app rebuilds it cleanly."""
    prev = settings.ENABLE_MCP
    settings.ENABLE_MCP = True
    # Reset the cached server singleton so subsequent create_app() calls
    # construct a fresh FastMCP rather than reusing a stale one.
    from api.mcp import server as _srv

    _srv._mcp_server = None
    yield
    settings.ENABLE_MCP = prev
    _srv._mcp_server = None


@pytest.fixture
def override_mcp_auth(with_mcp_enabled: None, monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    """Stub every built-in provider so the middleware sees exactly what
    the test sets. Returns a setter — tests call it with an
    ``MCPIdentity`` (or ``None`` to simulate the 401 path).
    """
    holder: dict[str, Optional[MCPIdentity]] = {"identity": None}

    def _stub(_scope: Any) -> Optional[MCPIdentity]:
        return holder["identity"]

    # Patch all three providers as the middleware imports them. Any of
    # them returning identity is enough; patching all three keeps the
    # test independent of which provider's gating condition would
    # normally fire under ENV=test.
    monkeypatch.setattr("api.mcp.auth.dev.resolve_identity", _stub)
    monkeypatch.setattr("api.mcp.auth.cloudflare.resolve_identity", _stub)
    monkeypatch.setattr("api.mcp.auth.oidc.resolve_identity", _stub)

    def _set(identity: Optional[MCPIdentity]) -> None:
        holder["identity"] = identity

    yield _set


def test_mcp_route_absent_when_disabled(app: FastAPI) -> None:
    """The /mcp route only exists when ENABLE_MCP is on. Default
    operators pay nothing — no route, no middleware."""
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/mcp" not in paths, f"/mcp route should not be mounted by default, got: {paths}"


def test_mcp_route_present_when_enabled(with_mcp_enabled: None) -> None:
    """ENABLE_MCP=True mounts the /mcp Starlette Route."""
    from api.app import create_app

    a = create_app(testing=True)
    paths = [getattr(r, "path", None) for r in a.routes]
    assert "/mcp" in paths


def test_unauthenticated_request_returns_401(
    override_mcp_auth: Any,
) -> None:
    """When every provider defers (returns None), the middleware emits a
    401 with a WWW-Authenticate Bearer challenge."""
    override_mcp_auth(None)
    from api.app import create_app

    a = create_app(testing=True)
    with TestClient(a, raise_server_exceptions=False) as client:
        r = client.post("/mcp", json={})
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate", "").startswith("Bearer ")


def test_authenticated_request_proceeds_past_middleware(
    db: Db,
    user: OktaUser,
    override_mcp_auth: Any,
) -> None:
    """An identity returned by any provider gets us past the 401 gate.
    We only verify that the response is NOT a 401 — the actual MCP
    JSON-RPC framing isn't part of this contract; that's tested by the
    direct-call tool tests below."""
    db.session.add(user)
    db.session.commit()
    override_mcp_auth(MCPIdentity(user_id=user.id, scopes=ALL_V1_SCOPES))
    from api.app import create_app

    a = create_app(testing=True)
    with TestClient(a, raise_server_exceptions=False) as client:
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        # We accept any non-401 — FastMCP may 400/406 on a malformed
        # body, but it's past the auth gate, which is what we care about.
        assert r.status_code != 401, f"Auth gate rejected a valid identity: {r.status_code} {r.text}"


def test_provider_chain_first_non_none_wins(
    with_mcp_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The middleware walks dev → cloudflare → oidc in that order and
    takes the first non-None identity. Exercises the chain logic
    directly against ``MCPAuthMiddleware`` rather than a TestClient so
    each iteration is independent of FastMCP's session-manager lifecycle
    (which can only ``.run()`` once per instance).
    """
    dev_id = MCPIdentity(user_id="dev-user", scopes=ALL_V1_SCOPES)
    cf_id = MCPIdentity(user_id="cf-user", scopes=ALL_V1_SCOPES)
    oidc_id = MCPIdentity(user_id="oidc-user", scopes=ALL_V1_SCOPES)

    captured: dict[str, Any] = {}

    async def _inner_app(_scope: Any, _recv: Any, _send: Any) -> None:
        # The middleware sets the identity ContextVar before delegating
        # — capture it here so the test can assert which provider's
        # result won.
        from api.mcp.auth import get_mcp_identity

        captured["identity"] = get_mcp_identity()

    from api.mcp.server import MCPAuthMiddleware

    middleware = MCPAuthMiddleware(_inner_app)

    async def _async_send(_msg: Any) -> None:
        return None

    async def _async_receive() -> Any:
        return {"type": "http.request"}

    async def _run() -> Optional[MCPIdentity]:
        captured.clear()
        await middleware({"type": "http", "path": "/mcp", "headers": []}, _async_receive, _async_send)
        return captured.get("identity")

    import asyncio

    # Dev wins outright; CF and OIDC don't get called.
    monkeypatch.setattr("api.mcp.auth.dev.resolve_identity", lambda _s: dev_id)
    monkeypatch.setattr("api.mcp.auth.cloudflare.resolve_identity", lambda _s: cf_id)
    monkeypatch.setattr("api.mcp.auth.oidc.resolve_identity", lambda _s: oidc_id)
    assert asyncio.run(_run()) == dev_id

    # Dev defers → CF wins; OIDC doesn't get called.
    monkeypatch.setattr("api.mcp.auth.dev.resolve_identity", lambda _s: None)
    assert asyncio.run(_run()) == cf_id

    # Dev + CF defer → OIDC wins.
    monkeypatch.setattr("api.mcp.auth.cloudflare.resolve_identity", lambda _s: None)
    assert asyncio.run(_run()) == oidc_id

    # All defer → identity remains None (the middleware short-circuits
    # with 401 in that case; the inner app is not called, so 'identity'
    # is missing from captured rather than set to None).
    monkeypatch.setattr("api.mcp.auth.oidc.resolve_identity", lambda _s: None)
    assert asyncio.run(_run()) is None


def _call_tool(mcp_server: Any, tool_name: str, **kwargs: Any) -> str:
    """Invoke a registered tool's Python function directly, bypassing
    the JSON-RPC framing. The tool's authorization checks fire as they
    would in production because they consult the active ContextVar."""
    tool = mcp_server._tool_manager._tools[tool_name]
    return tool.fn(**kwargs)


def test_read_tool_requires_read_all_scope(
    with_mcp_enabled: None,
    db: Db,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    """A token without read_all gets an error envelope from a read tool;
    a token with read_all gets data."""
    db.session.add_all([user, okta_group])
    db.session.commit()
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    # No scope → error envelope.
    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset()))
    try:
        result = _call_tool(mcp, "list_groups")
    finally:
        from api.mcp.auth import reset_mcp_identity

        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" in payload, f"expected error envelope, got: {payload}"
    assert "read_all" in payload["error"]

    # With read_all → data.
    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_READ_ALL})))
    try:
        result = _call_tool(mcp, "list_groups")
    finally:
        from api.mcp.auth import reset_mcp_identity

        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "results" in payload
    assert isinstance(payload["results"], list)


def test_write_tool_requires_create_requests_scope(
    with_mcp_enabled: None,
    db: Db,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    """create_access_request enforces the create_requests scope. A
    read-only token gets an error envelope; granting the scope produces
    a request."""
    db.session.add_all([user, okta_group])
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    # Read-only token — cannot create.
    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_READ_ALL})))
    try:
        result = _call_tool(mcp, "create_access_request", group_id=okta_group.id, reason="testing")
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" in payload
    assert "create_requests" in payload["error"]

    # Token that carries both scopes — request goes through.
    token = set_mcp_identity(
        MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_READ_ALL, MCP_SCOPE_CREATE_REQUESTS}))
    )
    try:
        result = _call_tool(mcp, "create_access_request", group_id=okta_group.id, reason="legitimate ask")
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" not in payload, f"unexpected error from create_access_request: {payload}"
    assert payload["requester_user_id"] == user.id
    assert payload["requested_group_id"] == okta_group.id
    assert payload["status"] == "PENDING"


def _make_access_admin(db: Db) -> OktaUser:
    """Return the bootstrap Access admin (seeded by the `db` fixture).
    Conftest's setup adds them as a *member* (not owner) of
    App-Access-Owners — that's the membership pattern is_access_admin
    checks for, so no additional wiring is needed."""
    admin = db.session.query(OktaUser).filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    assert admin is not None
    return admin


def test_cloudflare_fallback_read_only_opt_in(
    db: Db,
    user: OktaUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can pin `MCP_FALLBACK_SCOPES=read_all` to get
    read-only MCP sessions — useful for operators who don't want LLM
    agents filing requests via MCP. Verifies the read-only configuration
    actually disables write capability."""
    db.session.add(user)
    db.session.commit()

    # CF provider gates on CLOUDFLARE_TEAM_DOMAIN being set.
    monkeypatch.setattr(settings, "CLOUDFLARE_TEAM_DOMAIN", "example.cloudflareaccess.com")
    # Operator-pinned read-only fallback.
    monkeypatch.setattr(settings, "MCP_FALLBACK_SCOPES", "read_all")

    from api.mcp.auth.cloudflare import resolve_identity

    # Bypass JWT verification + fake a CF-Access-Jwt-Assertion header.
    monkeypatch.setattr(
        "api.mcp.auth.cloudflare.verify_cloudflare_token",
        lambda token: {"email": user.email},
    )
    scope: dict[str, Any] = {
        "headers": [(b"cf-access-jwt-assertion", b"any-token")],
    }
    identity = resolve_identity(scope=scope)
    assert identity is not None
    assert identity.scopes == frozenset({MCP_SCOPE_READ_ALL})
    # Critical: writes are NOT in this read-only fallback.
    assert MCP_SCOPE_CREATE_REQUESTS not in identity.scopes


def test_cloudflare_fallback_honours_operator_config(
    db: Db,
    user: OktaUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default fallback: `MCP_FALLBACK_SCOPES=read_all,create_requests`
    grants both scopes on CF-issued tokens that don't carry a scope
    claim, enabling read and write tools. Mirrors the shipped default."""
    db.session.add(user)
    db.session.commit()

    monkeypatch.setattr(settings, "CLOUDFLARE_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setattr(settings, "MCP_FALLBACK_SCOPES", "read_all,create_requests")

    from api.mcp.auth.cloudflare import resolve_identity

    monkeypatch.setattr(
        "api.mcp.auth.cloudflare.verify_cloudflare_token",
        lambda token: {"email": user.email},
    )
    scope: dict[str, Any] = {
        "headers": [(b"cf-access-jwt-assertion", b"any-token")],
    }
    identity = resolve_identity(scope=scope)
    assert identity is not None
    assert identity.scopes == frozenset({MCP_SCOPE_READ_ALL, MCP_SCOPE_CREATE_REQUESTS})


def test_cloudflare_fallback_empty_string_fails_closed(
    db: Db,
    user: OktaUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP_FALLBACK_SCOPES="" is the strict-mode posture: tokens
    without an explicit scope claim get an empty scope set and every
    require_scope check fails. Right answer once the provider starts
    emitting scope claims."""
    db.session.add(user)
    db.session.commit()

    monkeypatch.setattr(settings, "CLOUDFLARE_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setattr(settings, "MCP_FALLBACK_SCOPES", "")

    from api.mcp.auth.cloudflare import resolve_identity

    monkeypatch.setattr(
        "api.mcp.auth.cloudflare.verify_cloudflare_token",
        lambda token: {"email": user.email},
    )
    scope: dict[str, Any] = {
        "headers": [(b"cf-access-jwt-assertion", b"any-token")],
    }
    identity = resolve_identity(scope=scope)
    assert identity is not None
    assert identity.scopes == frozenset()


def test_cloudflare_explicit_scope_claim_overrides_fallback(
    db: Db,
    user: OktaUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the token carries an explicit scope claim, the fallback
    is irrelevant — token wins. Sanity check for the day CF Managed
    OAuth ships scope claims; nothing in our code needs to change at
    that point, the fallback just stops firing."""
    db.session.add(user)
    db.session.commit()

    monkeypatch.setattr(settings, "CLOUDFLARE_TEAM_DOMAIN", "example.cloudflareaccess.com")
    # Permissive fallback — but the token's explicit scope should win.
    monkeypatch.setattr(settings, "MCP_FALLBACK_SCOPES", "read_all,create_requests")

    from api.mcp.auth.cloudflare import resolve_identity

    monkeypatch.setattr(
        "api.mcp.auth.cloudflare.verify_cloudflare_token",
        lambda token: {"email": user.email, "scope": "read_all"},
    )
    scope: dict[str, Any] = {
        "headers": [(b"cf-access-jwt-assertion", b"any-token")],
    }
    identity = resolve_identity(scope=scope)
    assert identity is not None
    assert identity.scopes == frozenset({MCP_SCOPE_READ_ALL})
    assert MCP_SCOPE_CREATE_REQUESTS not in identity.scopes


def test_dev_provider_resolves_in_development(
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In ENV=development, the dev provider resolves CURRENT_OKTA_USER_EMAIL
    to an OktaUser and grants the full v1 scope set so local testing can
    exercise both read and write tools without a faked CF JWT."""
    admin = db.session.query(OktaUser).filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    assert admin is not None

    monkeypatch.setattr(settings, "ENV", "development")

    from api.mcp.auth.dev import resolve_identity

    identity = resolve_identity(scope={"headers": []})
    assert identity is not None
    assert identity.user_id == admin.id
    assert identity.scopes == ALL_V1_SCOPES


def test_dev_provider_defers_outside_dev_or_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In any production-style ENV, the dev provider returns None so the
    CF or OIDC provider gets a chance. The dev provider is safe to call
    unconditionally because of this guard."""
    monkeypatch.setattr(settings, "ENV", "production")

    from api.mcp.auth.dev import resolve_identity

    assert resolve_identity(scope={"headers": []}) is None


def test_oidc_provider_defers_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without OIDC_SERVER_METADATA_URL, the OIDC provider returns None
    immediately so it doesn't fight a CF deployment."""
    monkeypatch.setattr(settings, "OIDC_SERVER_METADATA_URL", None)
    from api.mcp.auth.oidc import resolve_identity

    assert resolve_identity(scope={"headers": []}) is None


def test_oidc_provider_defers_when_no_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OIDC configured but no Authorization header → defer (None)."""
    monkeypatch.setattr(settings, "OIDC_SERVER_METADATA_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setattr(settings, "OIDC_MCP_AUDIENCE", "access-mcp")
    from api.mcp.auth.oidc import resolve_identity

    assert resolve_identity(scope={"headers": []}) is None
    # Also: a non-bearer Authorization header → still defer.
    assert resolve_identity(scope={"headers": [(b"authorization", b"Basic abc")]}) is None


def test_oidc_provider_resolves_valid_token(
    db: Db,
    user: OktaUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a verified token's email claim resolves to an
    OktaUser; the token's scope claim becomes the identity's scopes."""
    db.session.add(user)
    db.session.commit()
    monkeypatch.setattr(settings, "OIDC_SERVER_METADATA_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setattr(settings, "OIDC_MCP_AUDIENCE", "access-mcp")

    from api.mcp.auth import oidc as oidc_provider

    # Bypass the JWKS fetch + JWT verification — we're testing the
    # post-verify resolution, not crypto.
    monkeypatch.setattr(
        oidc_provider,
        "_get_metadata_and_jwks",
        lambda: (
            {"issuer": "https://idp.example", "jwks_uri": "x", "id_token_signing_alg_values_supported": ["RS256"]},
            None,
        ),
    )

    class _Key:
        key = "fake-key"

    class _StubJWKS:
        def get_signing_key_from_jwt(self, _token: str) -> Any:
            return _Key()

    # Patch the JWKS client so signing-key lookup doesn't hit the network.
    monkeypatch.setattr(
        oidc_provider,
        "_get_metadata_and_jwks",
        lambda: (
            {"issuer": "https://idp.example", "jwks_uri": "x", "id_token_signing_alg_values_supported": ["RS256"]},
            _StubJWKS(),
        ),
    )

    import jwt as _jwt

    monkeypatch.setattr(
        _jwt,
        "decode",
        lambda *args, **kwargs: {"email": user.email, "scope": "read_all create_requests"},
    )

    scope: dict[str, Any] = {
        "headers": [(b"authorization", b"Bearer any-token")],
    }
    identity = oidc_provider.resolve_identity(scope=scope)
    assert identity is not None
    assert identity.user_id == user.id
    assert identity.scopes == ALL_V1_SCOPES


def test_oidc_provider_rejects_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token that fails verification → defer (None), no raise."""
    monkeypatch.setattr(settings, "OIDC_SERVER_METADATA_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setattr(settings, "OIDC_MCP_AUDIENCE", "access-mcp")

    from api.mcp.auth import oidc as oidc_provider

    class _Key:
        key = "fake-key"

    class _StubJWKS:
        def get_signing_key_from_jwt(self, _token: str) -> Any:
            return _Key()

    monkeypatch.setattr(
        oidc_provider,
        "_get_metadata_and_jwks",
        lambda: (
            {"issuer": "https://idp.example", "jwks_uri": "x", "id_token_signing_alg_values_supported": ["RS256"]},
            _StubJWKS(),
        ),
    )

    import jwt as _jwt

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise _jwt.InvalidAudienceError("audience mismatch")

    monkeypatch.setattr(_jwt, "decode", _raise)

    scope: dict[str, Any] = {
        "headers": [(b"authorization", b"Bearer any-token")],
    }
    assert oidc_provider.resolve_identity(scope=scope) is None


def test_config_validation_rejects_both_cf_and_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENABLE_MCP with both CLOUDFLARE_TEAM_DOMAIN and
    OIDC_SERVER_METADATA_URL set is a configuration mistake — pick
    one auth model for /mcp."""
    from api.config import Settings, _validate_mcp_auth_settings

    s = Settings()
    monkeypatch.setattr(s, "ENABLE_MCP", True)
    monkeypatch.setattr(s, "CLOUDFLARE_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setattr(s, "OIDC_SERVER_METADATA_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setattr(s, "OIDC_MCP_AUDIENCE", "access-mcp")
    with pytest.raises(ValueError, match="pick one"):
        _validate_mcp_auth_settings(s)


def test_config_validation_requires_oidc_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENABLE_MCP with OIDC_SERVER_METADATA_URL but no OIDC_MCP_AUDIENCE
    is rejected at startup — skipping aud validation is a confused-deputy
    foot-gun."""
    from api.config import Settings, _validate_mcp_auth_settings

    s = Settings()
    monkeypatch.setattr(s, "ENABLE_MCP", True)
    monkeypatch.setattr(s, "CLOUDFLARE_TEAM_DOMAIN", None)
    monkeypatch.setattr(s, "OIDC_SERVER_METADATA_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setattr(s, "OIDC_MCP_AUDIENCE", None)
    with pytest.raises(ValueError, match="OIDC_MCP_AUDIENCE"):
        _validate_mcp_auth_settings(s)


def test_config_validation_no_op_when_mcp_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ENABLE_MCP=false, the MCP auth validators don't fire — an
    operator running plain REST under both CF and OIDC for their REST
    surface is fine, only the MCP path imposes the mutual-exclusion."""
    from api.config import Settings, _validate_mcp_auth_settings

    s = Settings()
    monkeypatch.setattr(s, "ENABLE_MCP", False)
    monkeypatch.setattr(s, "CLOUDFLARE_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setattr(s, "OIDC_SERVER_METADATA_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setattr(s, "OIDC_MCP_AUDIENCE", None)
    # Should not raise.
    _validate_mcp_auth_settings(s)


def test_create_role_request_denies_non_owner(
    with_mcp_enabled: None,
    db: Db,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    """A user who does not own the role gets a 403-equivalent error
    envelope — mirrors POST /api/role-requests behaviour. The Access
    admin seeded by the test harness is intentionally not assigned as
    a role owner here, so the user has no path to authorization."""
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        result = _call_tool(
            mcp,
            "create_role_request",
            role_id=role_group.id,
            group_id=okta_group.id,
            reason="non-owner test",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" in payload, f"expected 403-equivalent error, got: {payload}"
    assert payload["error"] == "Current user is not allowed to perform this action"


def test_create_role_request_allowed_for_role_owner(
    with_mcp_enabled: None,
    db: Db,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    """A user who owns the role can submit a role request — same path
    POST /api/role-requests takes."""
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False,
    ).execute()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        result = _call_tool(
            mcp,
            "create_role_request",
            role_id=role_group.id,
            group_id=okta_group.id,
            reason="legitimate role-to-group grant",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" not in payload, payload
    assert payload["requester_user_id"] == user.id
    assert payload["requester_role_id"] == role_group.id
    assert payload["requested_group_id"] == okta_group.id
    assert payload["status"] == "PENDING"


def test_create_role_request_allowed_for_access_admin(
    with_mcp_enabled: None,
    db: Db,
    role_group: RoleGroup,
    okta_group: OktaGroup,
) -> None:
    """Access admins can submit role requests against any role even
    without being an owner of it — can_manage_group falls through to
    is_access_admin."""
    db.session.add_all([role_group, okta_group])
    db.session.commit()
    admin = _make_access_admin(db)
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=admin.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        result = _call_tool(
            mcp,
            "create_role_request",
            role_id=role_group.id,
            group_id=okta_group.id,
            reason="admin override",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" not in payload, payload
    assert payload["requester_user_id"] == admin.id


def test_create_role_request_rejects_role_as_target(
    with_mcp_enabled: None,
    db: Db,
    role_group: RoleGroup,
    user: OktaUser,
) -> None:
    """A role cannot be the target of a role request — roles cannot
    contain other roles. Use a second role as the target to exercise
    the check; the requester is a role owner of the first role so the
    authorization check passes and we reach the type guard."""
    other_role = RoleGroupFactory.build(name="Role-OtherRole", description="other role for testing")
    db.session.add_all([user, role_group, other_role])
    db.session.commit()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False,
    ).execute()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        result = _call_tool(
            mcp,
            "create_role_request",
            role_id=role_group.id,
            group_id=other_role.id,
            reason="should be rejected",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" in payload, payload
    assert "role" in payload["error"].lower()


def test_create_role_request_requires_create_requests_scope(
    with_mcp_enabled: None,
    db: Db,
    role_group: RoleGroup,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    """A read-only token cannot submit role requests even if the user
    is a role owner — scope check fires before authorization."""
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    ModifyGroupUsers(
        group=role_group,
        members_to_add=[user.id],
        owners_to_add=[user.id],
        sync_to_okta=False,
    ).execute()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_READ_ALL})))
    try:
        result = _call_tool(
            mcp,
            "create_role_request",
            role_id=role_group.id,
            group_id=okta_group.id,
            reason="should be blocked by scope",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" in payload
    assert "create_requests" in payload["error"]


def test_create_group_request_allowed_for_authenticated_user(
    with_mcp_enabled: None,
    db: Db,
    user: OktaUser,
) -> None:
    """Group requests are open to any authenticated, non-deleted user
    — same gate as POST /api/group-requests."""
    db.session.add(user)
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        result = _call_tool(
            mcp,
            "create_group_request",
            group_name="SomeNewGroup",
            group_type="okta_group",
            reason="need a new team group",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" not in payload, payload
    assert payload["requested_group_name"] == "SomeNewGroup"
    assert payload["requested_group_type"] == "okta_group"
    assert payload["status"] == "PENDING"


def test_create_group_request_requires_app_id_for_app_group(
    with_mcp_enabled: None,
    db: Db,
    user: OktaUser,
) -> None:
    """app_group requests must carry a valid app_id — REST handler
    raises 400 here, the MCP tool returns an error envelope with the
    same message."""
    db.session.add(user)
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        # Missing app_id on app_group request — fails at body validation
        # because the discriminated union variant requires it.
        result = _call_tool(
            mcp,
            "create_group_request",
            group_name="App-Foo-Newgrp",
            group_type="app_group",
            reason="missing app_id",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" in payload, payload


def test_create_group_request_rejects_unknown_app_id(
    with_mcp_enabled: None,
    db: Db,
    user: OktaUser,
) -> None:
    """A well-formed app_group request whose app_id doesn't resolve
    surfaces 'App not found' — mirrors POST /api/group-requests."""
    db.session.add(user)
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    try:
        result = _call_tool(
            mcp,
            "create_group_request",
            group_name="App-Foo-Newgrp",
            group_type="app_group",
            app_id="does-not-exist",
            reason="unknown app",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert payload.get("error") == "App not found"


def test_create_group_request_for_app_group(
    with_mcp_enabled: None,
    db: Db,
    user: OktaUser,
    access_app: App,
) -> None:
    """Happy-path for an app_group request: well-formed body, real
    app_id, returns a PENDING GroupRequest pointing at the app."""
    db.session.add_all([user, access_app])
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    token = set_mcp_identity(MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_CREATE_REQUESTS})))
    # Use the prefix matching this app so it passes the name pattern.
    target_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Newgrp"
    try:
        result = _call_tool(
            mcp,
            "create_group_request",
            group_name=target_name,
            group_type="app_group",
            app_id=access_app.id,
            reason="want this app group",
        )
    finally:
        reset_mcp_identity(token)
    payload = json.loads(result)
    assert "error" not in payload, payload
    assert payload["requested_group_type"] == "app_group"
    assert payload["requested_app_id"] == access_app.id


def test_mcp_write_tags_audit_log_with_source_mcp(
    with_mcp_enabled: None,
    db: Db,
    okta_group: OktaGroup,
    user: OktaUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The create_access_request operation emits its own audit log entry;
    we bind a RequestContext with source='mcp' the same way the ASGI
    middleware does in prod, then verify the resulting log carries
    source='mcp' rather than the default 'web'."""
    import logging

    db.session.add_all([user, okta_group])
    db.session.commit()
    from api.mcp.auth import reset_mcp_identity
    from api.mcp.server import create_mcp_server

    mcp = create_mcp_server()

    ctx = RequestContext(
        request_id="test-req-id",
        user_agent="mcp-test-agent",
        ip="127.0.0.1",
        source="mcp",
    )
    ctx_token = set_request_context(ctx)
    id_token = set_mcp_identity(
        MCPIdentity(user_id=user.id, scopes=frozenset({MCP_SCOPE_READ_ALL, MCP_SCOPE_CREATE_REQUESTS}))
    )
    try:
        with caplog.at_level(logging.INFO, logger="access.audit"):
            result = _call_tool(mcp, "create_access_request", group_id=okta_group.id, reason="audit-source-test")
    finally:
        reset_mcp_identity(id_token)
        from api.context import reset_request_context

        reset_request_context(ctx_token)
    payload = json.loads(result)
    assert "error" not in payload, payload

    # The operation logged at least one audit entry — find it and
    # confirm source was injected automatically from the ContextVar.
    audit_records = [r for r in caplog.records if r.name == "access.audit"]
    assert audit_records, "expected at least one access.audit log entry"
    audit_payload = json.loads(audit_records[-1].message)
    assert audit_payload.get("source") == "mcp", audit_payload
