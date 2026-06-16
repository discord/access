"""Cloudflare-Access MCP auth provider.

One of two built-in MCP auth providers (the other is OIDC in
``oidc.py``). Activates when the operator has configured Cloudflare
Access (``settings.CLOUDFLARE_TEAM_DOMAIN``); otherwise it returns
``None`` and the middleware tries the next provider.

JWT extraction:

  Cloudflare Access injects the edge-signed token on every authenticated
  request as the ``Cf-Access-Jwt-Assertion`` header. With CF Managed
  OAuth turned on for MCP clients, the same token comes through in the
  same header (per Peter's review note on the design doc, verified
  against the CF providers channel). We also accept
  ``Authorization: Bearer <token>`` because that's what the MCP OAuth
  spec mandates and what some clients send before CF rewrites — defense
  in depth against header-handling differences across CF Access
  policies.

Scopes:

  When the token carries a ``scope`` claim (space-separated) or
  ``scp`` claim (list), we parse it and surface those scopes verbatim
  on the returned ``MCPIdentity``. When the claim is absent — the
  typical case today because CF Managed OAuth doesn't yet support
  custom scopes — we fall back to the operator-configured scope set
  in ``settings.MCP_FALLBACK_SCOPES``.

  The default fallback is ``read_all,create_requests`` — both read
  and write tools are reachable. Every write tool still runs the same
  authorization predicate (Layer 2) and operation constraints (Layer 3)
  the matching REST endpoint applies, so this grants the user no
  capability they don't already have via REST. Operators who want
  read-only MCP sessions set ``MCP_FALLBACK_SCOPES=read_all``.
  Operators who want strict fail-closed behavior (no fallback at all)
  set ``MCP_FALLBACK_SCOPES=""`` — every CF token without an explicit
  scope claim will then fail every ``require_scope`` check, which is
  the right posture once your provider starts emitting scopes.
"""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.types import Scope

from api.auth.cloudflare import verify_cloudflare_token
from api.config import settings
from api.extensions import db as _db_shim
from api.mcp.auth import MCPIdentity
from api.models import OktaUser

logger = logging.getLogger(__name__)


def _configured_fallback_scopes() -> frozenset[str]:
    """Parse ``settings.MCP_FALLBACK_SCOPES`` (comma-separated) into a
    frozenset. Empty / whitespace-only entries are dropped, so
    ``"read_all, create_requests"`` and ``"read_all,create_requests"``
    are equivalent, and an empty string yields an empty set (the
    fail-closed posture)."""
    raw = settings.MCP_FALLBACK_SCOPES or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _extract_token(scope: Scope) -> str:
    """Pull the CF Access JWT off the ASGI scope.

    Mirrors ``api.auth.cloudflare.extract_token`` but reads from
    ``scope["headers"]`` directly because we don't have a Starlette
    ``Request`` object at this point — the FastMCP route runs outside
    FastAPI's dependency injection.
    """
    headers = dict(scope.get("headers", []))
    # CF Access edge-signed assertion (production CF deployments).
    val = headers.get(b"cf-access-jwt-assertion")
    if val:
        return val.decode()
    # CF "Cf-Access-Token" header (used by some service-token flows).
    val = headers.get(b"cf-access-token")
    if val:
        return val.decode()
    # Authorization: Bearer <jwt> — the MCP OAuth spec form. Some MCP
    # clients send this directly; CF may or may not rewrite it depending
    # on the access policy. Accept it as a fallback.
    val = headers.get(b"authorization")
    if val:
        decoded = val.decode()
        if decoded.lower().startswith("bearer "):
            return decoded[len("bearer ") :].strip()
    return ""


def _parse_scopes(claim: object) -> frozenset[str]:
    """Parse a ``scope`` / ``scp`` claim into a frozenset.

    OAuth tokens encode scopes as either a space-separated string
    (``scope``) or a list of strings (``scp``). Either is fine; anything
    else we treat as no declared scopes.
    """
    if isinstance(claim, str):
        return frozenset(s for s in claim.split() if s)
    if isinstance(claim, (list, tuple)):
        return frozenset(str(s) for s in claim if s)
    return frozenset()


def resolve_identity(scope: Scope) -> Optional[MCPIdentity]:
    """Resolve an ``MCPIdentity`` from a Cloudflare Access JWT.

    Returns ``None`` (defers to the next provider) when:
      - Cloudflare Access isn't configured on this deployment, OR
      - No CF/Bearer token is present on the request.

    Returns ``MCPIdentity`` on successful verification. Raises nothing on
    invalid tokens — the middleware turns "no identity from any
    provider" into a 401 with a single ``WWW-Authenticate`` header,
    which is what the MCP spec expects and keeps the error envelope
    consistent regardless of which provider rejected.
    """
    # No CF deployment → defer. This is the open-source escape hatch:
    # operators not using Cloudflare register a different provider.
    if not settings.CLOUDFLARE_TEAM_DOMAIN:
        return None

    token = _extract_token(scope)
    if not token:
        # No credential in any of the headers we recognise. Defer to the
        # next provider; the middleware emits 401 only when every
        # registered provider returns None.
        return None

    try:
        payload = verify_cloudflare_token(token)
    except HTTPException as e:
        # Token present but invalid. Log at debug — MCP clients retry
        # aggressively on auth failures and we don't want log spam — and
        # defer to the next provider. The middleware emits 401.
        logger.debug(f"CF Access token verification failed: {e.detail}")
        return None
    except jwt.PyJWTError:
        logger.debug("CF Access token failed JWT decode")
        return None

    db = _db_shim.session

    # MCP is human-user-only: the standard MCP deployment shape uses CF
    # Managed OAuth, which is OAuth-flow based and incompatible with
    # service tokens (no browser, no consent). Operators with a CF
    # Access policy that permits service tokens on /mcp will see those
    # requests rejected here. REST keeps its `common_name` handling in
    # ``api.auth.dependencies`` for non-MCP traffic.
    if "email" not in payload:
        logger.warning("CF Access JWT verified but carries no 'email' claim")
        return None
    user = db.scalars(
        select(OktaUser)
        .where(func.lower(OktaUser.email) == func.lower(payload["email"]))
        .where(OktaUser.deleted_at.is_(None))
    ).first()
    if user is None:
        logger.warning(f"CF Access JWT verified for email={payload['email']!r} but no matching active OktaUser exists")
        return None
    user_id = user.id

    scopes = _parse_scopes(payload.get("scope") or payload.get("scp"))
    if not scopes:
        # Token has no scope claim — fall back to the operator-
        # configured scope set. Default is `read_all,create_requests`;
        # operators who want read-only MCP set
        # MCP_FALLBACK_SCOPES=read_all. Once CF (or whatever provider
        # is in use) starts populating the scope claim, this branch
        # never fires and tokens control scope per session.
        scopes = _configured_fallback_scopes()

    return MCPIdentity(user_id=user_id, scopes=scopes)
