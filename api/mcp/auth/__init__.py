"""Identity types and helpers for the MCP auth layer.

Two built-in providers live alongside this module: ``cloudflare.py``
(Cloudflare Access JWT verification) and ``oidc.py`` (OIDC bearer-token
verification). ``dev.py`` is the local-dev shortcut. The ASGI auth
middleware in ``api.mcp.server`` calls each provider in order, stores
the resolved ``MCPIdentity`` on a ``ContextVar``, and the per-tool
helpers below read it. CF and OIDC are mutually exclusive — the config
validator in ``api/config.py`` rejects setups that configure both for
the MCP surface.

Tool handlers should call these helpers rather than reaching into
``request.state`` directly — that keeps tool code working when invoked
outside an HTTP request (e.g. from a test).
"""

from __future__ import annotations

import contextvars
import functools
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

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
# session. This also matches the default value of
# ``settings.MCP_FALLBACK_SCOPES`` (`read_all,create_requests`), which
# the Cloudflare provider uses for tokens without an explicit `scope`
# claim — operators who want read-only MCP sessions override that
# setting to `read_all`.
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

    Underlying primitive used by the ``requires_scope`` decorator and
    available to tests / non-tool helpers that need to assert scope
    without the decorator's tool-result envelope.
    """
    identity = _mcp_identity.get()
    if identity is None:
        raise MCPAuthenticationError("No MCP identity on the active context")
    if scope not in identity.scopes:
        raise MCPScopeError(f"This tool requires the '{scope}' scope; the active token does not carry it.")


F = TypeVar("F", bound=Callable[..., str])


def requires_scope(scope: str) -> Callable[[F], F]:
    """Decorator: enforce ``scope`` on the active MCP identity before the
    tool handler runs. On a missing scope, returns the canonical
    ``{"error": "..."}`` JSON envelope (same shape tool handlers use for
    every other failure) instead of raising — keeps the contract stable
    across FastMCP versions, which handle exceptions inconsistently.

    The MCP Python SDK ships a route-level ``RequireAuthMiddleware`` for
    a flat ``required_scopes`` list, but no per-tool decorator — and our
    v1 surface needs different scopes per tool (``read_all`` for reads,
    ``create_requests`` for writes), so we provide this.

    Apply between ``@mcp.tool(...)`` and the function definition::

        @mcp.tool(name="list_groups", ...)
        @requires_scope(MCP_SCOPE_READ_ALL)
        def list_groups(...): ...

    ``functools.wraps`` preserves the wrapped function's signature so
    FastMCP's schema introspection sees the original parameter list.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                require_scope(scope)
            except MCPScopeError as e:
                return json.dumps({"error": str(e)})
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


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
    "requires_scope",
    "reset_mcp_identity",
    "set_mcp_identity",
]
