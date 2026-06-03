"""Built-in OIDC MCP auth provider.

Activates when ``settings.OIDC_SERVER_METADATA_URL`` is set. The MCP path
uses a **resource-server** OIDC integration, not the browser
session-cookie flow ``api/auth/oidc.py`` runs for REST: MCP clients
aren't browsers and the MCP OAuth spec uses Bearer tokens in
``Authorization: Bearer <jwt>``. The OAuth dance (authorization code,
PKCE, etc.) is the MCP client's problem; this provider just verifies
the resulting access token.

Verification chain:

  1. Fetch the OIDC discovery document from
     ``OIDC_SERVER_METADATA_URL`` once (cached for the process lifetime
     of the worker; restart to refresh). Used to discover the
     ``jwks_uri``, ``issuer``, and ``id_token_signing_alg_values_supported``.
  2. ``PyJWKClient`` fetches and caches signing keys from ``jwks_uri``.
  3. ``jwt.decode`` validates the signature, ``iss``, ``exp``, ``nbf``,
     and ``aud`` against ``settings.OIDC_MCP_AUDIENCE`` (required —
     enforced at startup in ``api/config.py``).

Scopes come from the token's ``scope`` (space-separated) or ``scp``
(list) claim. When neither is present, we fall back to
``settings.MCP_FALLBACK_SCOPES`` (same shape the Cloudflare provider
uses) so operators whose IdP doesn't issue scope claims still get a
working session.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import httpx
import jwt
from sqlalchemy import func
from starlette.types import Scope

from api.config import settings
from api.extensions import db as _db_shim
from api.mcp.auth import MCPIdentity
from api.models import OktaUser

logger = logging.getLogger(__name__)

_metadata_lock = threading.RLock()
_metadata: Optional[dict[str, Any]] = None
_jwks_client: Optional[jwt.PyJWKClient] = None


def _get_metadata_and_jwks() -> tuple[dict[str, Any], jwt.PyJWKClient]:
    """Lazily fetch the OIDC discovery document and build a JWKS client.

    Cached for the worker lifetime; ``PyJWKClient`` does its own JWKS
    caching internally (default TTL is one hour). Restart the worker to
    pick up a new issuer URL or to force a metadata refresh.
    """
    global _metadata, _jwks_client
    with _metadata_lock:
        if _metadata is not None and _jwks_client is not None:
            return _metadata, _jwks_client
        if not settings.OIDC_SERVER_METADATA_URL:
            raise RuntimeError("OIDC provider used but OIDC_SERVER_METADATA_URL is unset")
        resp = httpx.get(settings.OIDC_SERVER_METADATA_URL, timeout=10.0)
        resp.raise_for_status()
        md = resp.json()
        if "jwks_uri" not in md or "issuer" not in md:
            raise RuntimeError(f"OIDC metadata at {settings.OIDC_SERVER_METADATA_URL!r} is missing jwks_uri or issuer")
        _metadata = md
        _jwks_client = jwt.PyJWKClient(md["jwks_uri"])
        return _metadata, _jwks_client


def _extract_bearer(scope: Scope) -> str:
    """Pull a Bearer token from the ``Authorization`` header."""
    headers = dict(scope.get("headers", []))
    val = headers.get(b"authorization")
    if not val:
        return ""
    decoded = val.decode()
    if not decoded.lower().startswith("bearer "):
        return ""
    return decoded[len("bearer ") :].strip()


def _parse_scopes(claim: object) -> frozenset[str]:
    if isinstance(claim, str):
        return frozenset(s for s in claim.split() if s)
    if isinstance(claim, (list, tuple)):
        return frozenset(str(s) for s in claim if s)
    return frozenset()


def _configured_fallback_scopes() -> frozenset[str]:
    raw = settings.MCP_FALLBACK_SCOPES or ""
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def resolve_identity(scope: Scope) -> Optional[MCPIdentity]:
    """Return an ``MCPIdentity`` for an OIDC-authenticated request, or
    ``None`` to defer (no OIDC config, no Bearer token, verification
    failure). Never raises for a missing credential — the middleware
    emits a 401 when every provider returns None.
    """
    if not settings.OIDC_SERVER_METADATA_URL:
        return None
    token = _extract_bearer(scope)
    if not token:
        return None

    try:
        md, jwks_client = _get_metadata_and_jwks()
    except Exception:
        logger.exception("OIDC metadata / JWKS bootstrap failed; treating as unauthenticated")
        return None

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientError as e:
        logger.debug(f"OIDC JWKS lookup failed: {e}")
        return None
    except jwt.PyJWTError as e:
        logger.debug(f"OIDC token header malformed: {e}")
        return None

    algorithms = md.get("id_token_signing_alg_values_supported") or ["RS256"]
    try:
        payload = jwt.decode(
            token,
            key=signing_key,
            algorithms=algorithms,
            audience=settings.OIDC_MCP_AUDIENCE,
            issuer=md["issuer"],
            leeway=settings.OIDC_CLOCK_SKEW,
        )
    except jwt.PyJWTError as e:
        logger.debug(f"OIDC token verification failed: {e}")
        return None

    email = payload.get("email")
    if not email:
        logger.warning("OIDC token verified but carries no 'email' claim")
        return None

    db = _db_shim.session
    user = (
        db.query(OktaUser)
        .filter(func.lower(OktaUser.email) == func.lower(email))
        .filter(OktaUser.deleted_at.is_(None))
        .first()
    )
    if user is None:
        logger.warning(f"OIDC token verified for email={email!r} but no matching active OktaUser exists")
        return None

    scopes = _parse_scopes(payload.get("scope") or payload.get("scp"))
    if not scopes:
        scopes = _configured_fallback_scopes()
    return MCPIdentity(user_id=user.id, scopes=scopes)
