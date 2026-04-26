"""OIDC integration via Authlib.

Replaces the legacy `flask_oidc` integration. Uses Authlib's `OAuth` client
plus Starlette's `SessionMiddleware` to manage the auth session cookie.
Endpoints:

  - `GET  /oidc/login`     — kicks off the auth flow, redirects to the IdP
  - `GET  /oidc/authorize` — receives the IdP callback, sets `session["userinfo"]`
  - `GET  /oidc/logout`    — clears the session

The auth dependency in `api.auth.dependencies.get_current_user_id` consults
`request.session["userinfo"]["email"]` when OIDC is configured.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from api.config import settings

logger = logging.getLogger(__name__)

oauth = OAuth()


def _parse_secrets(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError("OIDC_CLIENT_SECRETS must be a JSON object or path to one")


def _client_kwargs() -> dict:
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
    if "oidc" not in oauth._clients:  # type: ignore[attr-defined]
        oauth.register(name="oidc", **_client_kwargs())
    app.include_router(_router)


_router = APIRouter(prefix="/oidc", tags=["oidc"])


@_router.get("/login", name="oidc_login")
async def login(request: Request, next: Optional[str] = None) -> RedirectResponse:
    redirect_uri = settings.OIDC_OVERWRITE_REDIRECT_URI or str(request.url_for("oidc_authorize"))
    if next:
        request.session["oidc_next"] = next
    return await oauth.oidc.authorize_redirect(request, redirect_uri)  # type: ignore[no-any-return]


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
    request.session.pop("userinfo", None)
    # Optionally redirect to the IdP's end_session endpoint if configured.
    metadata = await oauth.oidc.load_server_metadata()
    end_session = metadata.get("end_session_endpoint")
    if end_session:
        params = {"post_logout_redirect_uri": str(request.url_for("spa", path="") or "/")}
        return RedirectResponse(url=f"{end_session}?{urlencode(params)}")
    return RedirectResponse(url="/")
