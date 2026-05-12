"""Identity types and helpers for the MCP auth layer.

The pluggy hookspec lives in ``api/plugins/mcp_auth.py``; concrete
implementations live alongside it in this package (the Cloudflare default
in ``cloudflare.py``). The ASGI auth middleware in ``api.mcp.server``
runs the hookcall, stores the resolved ``MCPIdentity`` on
``scope["state"]``, and propagates it to the per-tool helpers below via a
``ContextVar`` so handlers don't have to thread the Starlette request
through every call.

Tool handlers should call these helpers rather than reaching into
``request.state`` directly — that keeps tool code working when invoked
outside an HTTP request (e.g. from a test).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Optional

# Scope strings used by the v1 tool surface. Tools declare a required
# scope via ``@require_scope(MCP_SCOPE_READ_ALL)`` etc.; the
# ``require_scope`` helper checks against the resolved identity's scope
# set. The vocabulary is intentionally coarse — per-tool / per-endpoint
# scopes are deferred until we have a concrete use case that justifies
# the finer granularity.
MCP_SCOPE_READ_ALL = "read_all"
MCP_SCOPE_CREATE_REQUESTS = "create_requests"

# The full v1 scope set, kept as a named constant for tests and any
# operator-supplied provider that wants to issue a fully-scoped
# session. NOT the default fallback — the Cloudflare provider's
# fallback for tokens without a `scope` claim is operator-configurable
# via ``settings.MCP_FALLBACK_SCOPES`` and defaults to read-only
# (`read_all` only).
ALL_V1_SCOPES: frozenset[str] = frozenset({MCP_SCOPE_READ_ALL, MCP_SCOPE_CREATE_REQUESTS})


@dataclass(frozen=True)
class MCPIdentity:
    """Resolved identity for an authenticated MCP request.

    ``user_id`` is an ``OktaUser.id`` (or a service-token ``common_name``
    for Cloudflare service tokens — same shape ``api.auth.dependencies``
    uses for the REST path). ``scopes`` is whatever the auth provider
    extracted from the credential; empty means "no scopes declared,"
    which by default is treated as no permission to call any tool — see
    ``require_scope``.
    """

    user_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)


class MCPAuthenticationError(Exception):
    """Raised by tool helpers when no identity is present on the active
    context — should never happen in production because the ASGI auth
    middleware short-circuits with a 401 before the FastMCP dispatch
    runs, but the explicit error makes test failures legible.
    """


class MCPScopeError(Exception):
    """Raised by ``require_scope`` when the active identity does not
    carry the required scope. FastMCP catches this and returns it to the
    client as a tool error.
    """


# ContextVar set by the ASGI auth middleware before FastMCP dispatch and
# cleared on the way out. Per-tool helpers read it.
_mcp_identity: contextvars.ContextVar[Optional[MCPIdentity]] = contextvars.ContextVar(
    "access_mcp_identity", default=None
)


def set_mcp_identity(identity: Optional[MCPIdentity]) -> contextvars.Token[Optional[MCPIdentity]]:
    return _mcp_identity.set(identity)


def reset_mcp_identity(token: contextvars.Token[Optional[MCPIdentity]]) -> None:
    _mcp_identity.reset(token)


def get_mcp_identity() -> Optional[MCPIdentity]:
    return _mcp_identity.get()


def get_mcp_user_id() -> str:
    """Return the authenticated MCP user id or raise.

    Tool handlers call this instead of dipping into the request state so
    they remain callable from tests with an explicit ``set_mcp_identity``
    context. Raises ``MCPAuthenticationError`` outside an authenticated
    MCP request.
    """
    identity = _mcp_identity.get()
    if identity is None:
        raise MCPAuthenticationError("No MCP identity on the active context")
    return identity.user_id


def get_mcp_scopes() -> frozenset[str]:
    """Return the scope set of the active identity, or empty if none."""
    identity = _mcp_identity.get()
    if identity is None:
        return frozenset()
    return identity.scopes


def require_scope(scope: str) -> None:
    """Raise ``MCPScopeError`` if the active identity is missing ``scope``.

    Read tools call ``require_scope(MCP_SCOPE_READ_ALL)`` at the top of
    their handler. The write tool calls
    ``require_scope(MCP_SCOPE_CREATE_REQUESTS)``. We deliberately do NOT
    decorate handlers — keeping the check inline makes it visible at
    every call site, which matches the convention used elsewhere in the
    codebase where authorization is checked explicitly rather than via
    decorators.
    """
    identity = _mcp_identity.get()
    if identity is None:
        raise MCPAuthenticationError("No MCP identity on the active context")
    if scope not in identity.scopes:
        raise MCPScopeError(f"This tool requires the '{scope}' scope; the active token does not carry it.")


__all__ = [
    "ALL_V1_SCOPES",
    "MCPAuthenticationError",
    "MCPIdentity",
    "MCPScopeError",
    "MCP_SCOPE_CREATE_REQUESTS",
    "MCP_SCOPE_READ_ALL",
    "get_mcp_identity",
    "get_mcp_scopes",
    "get_mcp_user_id",
    "require_scope",
    "reset_mcp_identity",
    "set_mcp_identity",
]
