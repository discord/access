"""OIDC integration via Authlib.

Replaces the legacy `flask_oidc` integration. Uses Authlib's `OAuth` client
plus Starlette's `SessionMiddleware` to manage the auth session cookie.
Endpoints:

  - `GET  /oidc/login`     — kicks off the auth flow, redirects to the IdP
  - `GET  /oidc/authorize` — receives the IdP callback, sets `session["userinfo"]`
  - `GET  /oidc/logout`    — clears the session

The auth dependency in `api.auth.dependencies.get_current_user_id` consults
`request.session["userinfo"]["email"]` when OIDC is configured, or verifies an
`Authorization: Bearer` token via `verify_bearer_token` when
`OIDC_API_AUDIENCE` is set.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import jwt
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from api.config import settings

logger = logging.getLogger(__name__)

oauth = OAuth()


def _parse_secrets(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError("OIDC_CLIENT_SECRETS must be a JSON object or path to one")


def _client_kwargs() -> dict[str, Any]:
    secrets = _parse_secrets(settings.OIDC_CLIENT_SECRETS)
    web = secrets.get("web", secrets)
    kwargs = {
        "client_id": web.get("client_id"),
        "client_secret": web.get("client_secret"),
        "client_kwargs": {"scope": settings.OIDC_SCOPES},
    }
    if settings.OIDC_SERVER_METADATA_URL:
        kwargs["server_metadata_url"] = settings.OIDC_SERVER_METADATA_URL
    elif "server_metadata_url" in web:
        kwargs["server_metadata_url"] = web["server_metadata_url"]
    elif "issuer" in web:
        kwargs["server_metadata_url"] = f"{web['issuer'].rstrip('/')}/.well-known/openid-configuration"
    return kwargs


def register_oidc(app: FastAPI) -> None:
    """Register the OIDC OAuth client and mount the auth endpoints."""
    if "oidc" not in oauth._clients:
        oauth.register(name="oidc", **_client_kwargs())
    app.include_router(_router)


# PyJWKClient caches the IdP's signing keys itself; keep one per jwks_uri.
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


async def verify_bearer_token(token: str) -> dict[str, Any]:
    """Verify an OIDC bearer token presented to the REST API.

    Checks the signature against the IdP's JWKS (discovered through the same
    server metadata the login flow uses), plus `iss`, `exp`, `nbf`, and `aud`
    against `settings.OIDC_API_AUDIENCE`.

    Args:
        token: The raw JWT from the `Authorization: Bearer` header.

    Returns:
        The verified token claims.

    Raises:
        HTTPException: 403 if the token fails verification.
    """
    metadata = await oauth.oidc.load_server_metadata()
    jwks_uri, issuer = metadata.get("jwks_uri"), metadata.get("issuer")
    if not jwks_uri or not issuer:
        logger.error("OIDC server metadata is missing jwks_uri or issuer; rejecting bearer token")
        raise HTTPException(status_code=403, detail="Invalid OIDC bearer token")
    if jwks_uri not in _jwks_clients:
        _jwks_clients[jwks_uri] = jwt.PyJWKClient(jwks_uri)
    try:
        # The JWKS fetch is blocking network I/O; keep it off the event loop.
        signing_key = await asyncio.to_thread(_jwks_clients[jwks_uri].get_signing_key_from_jwt, token)
        return jwt.decode(
            token,
            key=signing_key.key,
            # Pin to the key's own algorithm so the token header can't pick a
            # weaker one (e.g. HS256 keyed with the public key).
            algorithms=[signing_key.algorithm_name],
            audience=settings.OIDC_API_AUDIENCE,
            issuer=issuer,
            leeway=settings.OIDC_CLOCK_SKEW,
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=403, detail="Invalid OIDC bearer token") from e


_router = APIRouter(prefix="/oidc", tags=["oidc"])


def _is_safe_next(next_url: Optional[str]) -> bool:
    # Reject absolute URLs and protocol-relative paths so `next` cannot bounce
    # the post-auth redirect to a third-party host.
    if not next_url or not next_url.startswith("/"):
        return False
    if next_url.startswith("//") or next_url.startswith("/\\"):
        return False
    parsed = urlparse(next_url)
    return not parsed.scheme and not parsed.netloc


@_router.get("/login", name="oidc_login")
async def login(request: Request, next: Optional[str] = None) -> RedirectResponse:
    # The url_for fallback derives the callback from the inbound Host header.
    # ALLOWED_HOSTS (TrustedHostMiddleware) guards that surface; set
    # OIDC_OVERWRITE_REDIRECT_URI to pin a fixed callback behind a proxy.
    redirect_uri = settings.OIDC_OVERWRITE_REDIRECT_URI or str(request.url_for("oidc_authorize"))
    if _is_safe_next(next):
        request.session["oidc_next"] = next
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@_router.get("/authorize", name="oidc_authorize")
async def authorize(request: Request) -> RedirectResponse:
    try:
        token = await oauth.oidc.authorize_access_token(request)
    except Exception as e:  # pragma: no cover
        logger.warning("OIDC authorize failed: %s", e)
        raise HTTPException(status_code=403, detail="OIDC authorization failed") from e
    userinfo = token.get("userinfo")
    if userinfo is None:
        userinfo = await oauth.oidc.userinfo(token=token)
    request.session["userinfo"] = dict(userinfo)
    next_url = request.session.pop("oidc_next", None) or "/"
    return RedirectResponse(url=next_url)


@_router.get("/logout", name="oidc_logout")
async def logout(request: Request) -> RedirectResponse:
    # Clear the local session only. The user remains signed in to the IdP;
    # a true SSO logout would need `id_token_hint` plus a registered
    # `post_logout_redirect_uri`, which is out of scope here.
    request.session.clear()
    return RedirectResponse(url="/")
