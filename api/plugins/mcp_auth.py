"""Pluggy hookspec for MCP request authentication.

The embedded MCP server (gated behind ``ENABLE_MCP``) accepts requests via
Streamable HTTP. We do not want to hardcode Cloudflare Access as the only
auth path: the Access repo is open source and operators run their own
deployments (OIDC, mTLS, custom JWT issuers, …). The hookspec defined here
lets the Cloudflare default ship in the open-source repo while leaving
the seam open for any operator-supplied provider — they implement
``mcp_resolve_identity`` in their own plugin package and register via the
``access_mcp_auth`` setuptools entry-point.

Implementations are run in registration order and the **first non-None**
result wins (``firstresult=True``). The shipped Cloudflare provider opts
out by returning ``None`` when ``settings.CLOUDFLARE_TEAM_DOMAIN`` is
unset, so a non-CF deployment can register another provider without
fighting the default.

Scope: this hookspec is for **credential verification**, not for hosting
an OAuth/OIDC authorization server. It assumes the OAuth flow (if any)
runs in front of Access — typically a Cloudflare-Access-style OIDC
proxy that completes the dance with the upstream IdP and injects a
verified header into the request. The hookimpl then validates that
header and resolves it to an ``OktaUser``. Hosting ``/authorize``,
``/token``, dynamic client registration, or callback handling is out of
scope for this interface; an operator who wants Access itself to be the
authorization server needs to add their own router for those endpoints
in addition to a ``mcp_resolve_identity`` impl.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import pluggy

if TYPE_CHECKING:
    from starlette.types import Scope

    from api.mcp.auth import MCPIdentity

mcp_auth_plugin_name = "access_mcp_auth"
hookspec = pluggy.HookspecMarker(mcp_auth_plugin_name)
hookimpl = pluggy.HookimplMarker(mcp_auth_plugin_name)

logger = logging.getLogger(__name__)

_cached_mcp_auth_hook: pluggy.HookRelay | None = None


class MCPAuthPluginSpec:
    @hookspec(firstresult=True)
    def mcp_resolve_identity(self, scope: "Scope") -> Optional["MCPIdentity"]:
        """Verify the credential on an incoming MCP request and return the
        resolved Access user identity (plus any scopes parsed from the
        token).

        Implementations should:
          - Inspect the ASGI ``scope`` (headers, cookies) for their
            credential type.
          - Verify the credential (e.g. JWT signature + audience).
          - Resolve the email/identifier to an ``OktaUser``.
          - Return ``MCPIdentity`` with the user id and any scopes parsed
            from the token's ``scope`` claim.

        Return ``None`` to defer to the next registered provider — do
        **not** raise for a missing credential. A 401 is emitted by the
        ASGI auth middleware when every provider returns None.
        """


def get_mcp_auth_hook() -> pluggy.HookRelay:
    """Return the memoized ``access_mcp_auth`` hook relay.

    The default Cloudflare provider is registered explicitly so it ships
    in the open-source repo without depending on entry-point discovery.
    Operator-supplied providers register via the ``access_mcp_auth``
    setuptools entry point in their plugin package, the same as the
    notifications / app_group_lifecycle plugin types.
    """
    global _cached_mcp_auth_hook
    if _cached_mcp_auth_hook is not None:
        return _cached_mcp_auth_hook

    pm = pluggy.PluginManager(mcp_auth_plugin_name)
    pm.add_hookspecs(MCPAuthPluginSpec)

    # Register the default Cloudflare provider. Import is local so that a
    # bare `from api.plugins.mcp_auth import ...` doesn't pull in any of
    # the Cloudflare verification machinery unless this function actually
    # runs (i.e. ENABLE_MCP is true).
    from api.mcp.auth import cloudflare as _cf
    from api.mcp.auth import dev as _dev

    pm.register(_cf)
    # Dev provider is registered unconditionally; its hookimpl gates on
    # ENV ∈ {development, test}, so in any production-style environment
    # it returns None and defers. Locally, the CF provider opts out via
    # its own CLOUDFLARE_TEAM_DOMAIN check, so the two never compete.
    pm.register(_dev)

    count = pm.load_setuptools_entrypoints(mcp_auth_plugin_name)
    logger.info(f"Loaded {count} additional MCP auth provider(s)")

    _cached_mcp_auth_hook = pm.hook
    return _cached_mcp_auth_hook
