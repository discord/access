"""FastMCP server construction, ASGI auth middleware, and lifespan.

Wiring overview:

  1. ``create_mcp_server`` builds the singleton ``FastMCP`` with
     ``stateless_http=True`` and DNS-rebinding protection off (the
     deployment's edge — Cloudflare or otherwise — handles that
     perimeter). All tools and prompts are registered here.
  2. ``get_mcp_route`` returns a Starlette ``Route`` (not a ``Mount``)
     bound at ``/mcp`` so requests without a trailing slash work — some
     MCP clients POST to ``/mcp`` bare and ``Mount`` 405s on that path.
  3. ``MCPAuthMiddleware`` is an ASGI middleware that calls each of the
     built-in providers in ``api.mcp.auth`` (dev → cloudflare → oidc) in
     order, takes the first non-None ``MCPIdentity``, binds it to the
     per-request ContextVar in ``api.mcp.auth``, and swaps the active
     ``RequestContext`` for one tagged ``source="mcp"`` so the audit
     log can distinguish MCP-driven entries. Returns 401 when every
     provider returns None.
  4. ``mcp_lifespan`` is an async context manager that the FastAPI app
     enters during its lifespan so the FastMCP session manager's task
     group is alive while requests are served.

The FastAPI app-wide ``Depends(require_authenticated)`` does NOT
propagate into a Starlette ``Route`` mounted this way, so the MCP path
runs in its own auth/authz lane. AuthN is the built-in provider chain;
AuthZ is re-checked per tool against the existing bare predicates in
``api.auth.permissions``.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.config import settings
from api.context import RequestContext, reset_request_context, set_request_context
from api.mcp.auth import (
    MCPIdentity,
    reset_mcp_identity,
    set_mcp_identity,
)
from api.mcp.auth import cloudflare as cloudflare_provider
from api.mcp.auth import dev as dev_provider
from api.mcp.auth import oidc as oidc_provider

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"

# Module-level singleton so the lifespan and route helpers reference the
# same FastMCP instance. Constructed lazily by ``create_mcp_server`` and
# torn down by reset to None in tests if needed.
_mcp_server: FastMCP | None = None


def create_mcp_server() -> FastMCP:
    """Build the Access MCP server. Idempotent — repeat calls return
    the same instance so the lifespan and the route refer to the same
    session manager."""
    global _mcp_server
    if _mcp_server is not None:
        return _mcp_server

    # Local import to keep ``api.mcp.prompts`` out of the module-load
    # chain when ENABLE_MCP is false. The prompts module's body imports
    # nothing heavy, but the pattern matches what we do for tools.
    from api.mcp.prompts import ACCESS_MCP_INSTRUCTIONS, register_prompts
    from api.mcp.tools import register_tools

    # DNS-rebinding protection is driven by ``settings.MCP_ALLOWED_HOSTS``.
    # Empty (default) → protection OFF; structural defenses (auth
    # middleware, CORS, no browser MCP client) carry the threat model
    # for prod deployments behind a reverse proxy that rewrites Host.
    # Non-empty → protection ON, with the configured hosts as the
    # allowlist (operators set this for defense-in-depth or for local
    # dev with a browser open).
    allowed_hosts = [h.strip() for h in (settings.MCP_ALLOWED_HOSTS or "").split(",") if h.strip()]
    mcp = FastMCP(
        "Access",
        instructions=ACCESS_MCP_INSTRUCTIONS,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(allowed_hosts),
            allowed_hosts=allowed_hosts,
        ),
    )

    register_tools(mcp)
    register_prompts(mcp)

    logger.info(
        f"Access MCP server initialized with {len(mcp._tool_manager._tools)} tools "
        f"and {len(mcp._prompt_manager._prompts)} prompts"
    )

    _mcp_server = mcp
    return mcp


def get_mcp_server() -> FastMCP:
    if _mcp_server is None:
        raise RuntimeError("MCP server has not been created. Call create_mcp_server() first.")
    return _mcp_server


def get_mcp_route() -> Route:
    """Return a Starlette Route bound at ``/mcp`` (no trailing slash).

    ``StreamableHTTPASGIApp`` is the raw ASGI handler the session manager
    exposes; ``Route`` registers it as a normal Starlette route so the
    request actually traverses the outer middleware stack (RequestId,
    CacheControl, etc.) on the way in and out. A ``Mount`` would require
    ``/mcp/`` and 405 on bare-``/mcp`` POSTs, which is what some MCP
    clients send.
    """
    mcp = get_mcp_server()
    # Force creation of the session manager — ``streamable_http_app``
    # is a builder that initializes it lazily.
    mcp.streamable_http_app()
    handler = StreamableHTTPASGIApp(mcp.session_manager)
    # ``methods=None`` allows every HTTP verb (GET, POST, DELETE) the MCP
    # Streamable-HTTP transport uses for session resumption / aborts.
    return Route(MCP_PATH, endpoint=handler, methods=None)


@asynccontextmanager
async def mcp_lifespan() -> AsyncIterator[None]:
    """Run the FastMCP session manager alongside the parent app.

    Entered from ``api.app.create_app``'s lifespan when ``ENABLE_MCP`` is
    true. Without this the session manager's anyio TaskGroup never
    starts and the first ``/mcp`` request 500s with "Task group is not
    initialized."
    """
    mcp = get_mcp_server()
    mcp.streamable_http_app()  # idempotent — guarantees session_manager exists
    async with mcp.session_manager.run():
        logger.info("MCP session manager started")
        try:
            yield
        finally:
            logger.info("MCP session manager shutting down")


# --- ASGI auth middleware ---------------------------------------------------


def _client_ip_from_headers(scope: Scope) -> Optional[str]:
    """Best-effort client IP, mirroring api.middleware._client_ip but
    reading directly off the ASGI scope (no Starlette Request available
    here)."""
    headers = dict(scope.get("headers", []))
    fwd = headers.get(b"x-forwarded-for")
    if fwd:
        return fwd.decode().split(",")[0].strip()
    real = headers.get(b"x-real-ip")
    if real:
        return real.decode()
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return str(client[0])
    return None


def _user_agent_from_headers(scope: Scope) -> Optional[str]:
    headers = dict(scope.get("headers", []))
    ua = headers.get(b"user-agent")
    return ua.decode() if ua else None


async def _send_401(send: Send, scope: Scope) -> None:
    # Per the MCP OAuth spec, 401 responses include a WWW-Authenticate
    # header so the client knows where to send the user for auth. We
    # don't currently advertise a discovery endpoint because Cloudflare
    # Managed OAuth handles redirection at the edge — operators with a
    # different provider can add ``as_uri="..."`` as part of their
    # provider's response shaping. The middleware-level header is the
    # fallback for direct hits.
    resp = JSONResponse(
        status_code=401,
        content={"message": "Unauthorized"},
        headers={"WWW-Authenticate": 'Bearer realm="access"'},
    )
    await resp(scope, receive=lambda: _empty_recv(), send=send)


async def _empty_recv() -> Message:
    # Starlette responses call receive() at least once for the lifecycle
    # event; an empty disconnect is fine for a one-shot 401.
    return {"type": "http.disconnect"}


class MCPAuthMiddleware:
    """Resolve MCP identity (via pluggy), bind it to the active context,
    and tag the audit-log source as ``mcp`` for the duration of the
    request.

    Wrapped as a plain ASGI middleware rather than a
    ``BaseHTTPMiddleware``: we need direct access to ``scope["headers"]``
    (no Starlette ``Request`` materialization) and we want to bind
    ContextVars on the *outer* dispatch so they're visible during the
    FastMCP handler's threadpool work too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Only intercept /mcp paths. Every other route runs through the
        # normal FastAPI auth gate.
        if path != MCP_PATH and not path.startswith(MCP_PATH + "/"):
            await self.app(scope, receive, send)
            return

        # Try each built-in provider in order. First non-None identity
        # wins. The dev provider gates on ENV={development,test}; the CF
        # and OIDC providers are mutually exclusive in production (the
        # config validator in api/config.py rejects setups that
        # configure both).
        identity: Optional[MCPIdentity] = None
        for provider in (dev_provider, cloudflare_provider, oidc_provider):
            try:
                identity = provider.resolve_identity(scope)
            except Exception:
                # Providers shouldn't raise — they return None on
                # missing or invalid credentials. A raised exception is
                # a provider bug; log it and continue down the chain.
                logger.exception("MCP auth provider %s raised; continuing", provider.__name__)
                continue
            if identity is not None:
                break

        if identity is None:
            await _send_401(send, scope)
            return

        # Bind the identity + a RequestContext that's tagged as MCP-
        # sourced. The REST path's RequestContextMiddleware also runs
        # on this route (it's a BaseHTTPMiddleware mounted at the
        # FastAPI app level), but it sets source="web" by default;
        # rebinding here overrides for the duration of the request.
        ip = _client_ip_from_headers(scope)
        ua = _user_agent_from_headers(scope)
        request_id = scope.get("state", {}).get("request_id") if isinstance(scope.get("state"), dict) else None
        ctx = RequestContext(
            request_id=request_id or uuid.uuid4().hex,
            user_agent=ua,
            ip=ip,
            source="mcp",
        )

        identity_token = set_mcp_identity(identity)
        ctx_token = set_request_context(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_context(ctx_token)
            reset_mcp_identity(identity_token)
