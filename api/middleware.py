"""ASGI middleware for the Access API.

- RequestIdMiddleware: tags each request with a UUID, stored on `request.state`
  and used as the SQLAlchemy session scope key.
- RequestContextMiddleware: builds a `RequestContext` from the request headers
  and binds it to a ContextVar so audit logging in operations can read it.
- RequireAuthMiddleware: enforces authentication on every request except a
  small allowlist (health, OIDC login). Static assets — the React SPA mount
  at `/` — go through this gate too, since middleware intercepts mounts that
  the FastAPI dependency chain skips.
- SecurityHeadersMiddleware: emits the CSP / X-Frame-Options / Referrer-Policy
  / X-Content-Type-Options headers on every response.
- CacheControlMiddleware: emits no-store cache headers on `/api/*` responses.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from api.config import settings
from api.context import RequestContext, reset_request_context, set_request_context
from api.extensions import _session_scope, db

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self' *.ingest.sentry.io; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'"
)

# Relaxed CSP for development: allow Swagger UI assets served from CDN by
# FastAPI's auto-generated `/api/docs` page.
DEBUG_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self' *.ingest.sentry.io; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'"
)


def _client_ip(request: Request) -> str | None:
    headers = request.headers
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip
    if request.client is not None:
        return request.client.host
    return None


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Tag each request with a UUID and bind the SQLAlchemy session scope.

    `_session_scope` is the `scopefunc` for the application's
    `scoped_session`. Setting it per request guarantees concurrent requests
    each get their own Session — without this, every request shares the
    `"__default__"` key and SQLAlchemy raises "This session is provisioning
    a new connection; concurrent operations are not permitted" the moment
    two requests run queries simultaneously.

    If an outer caller (tests, CLI) has already set a scope, leave it alone
    so request handlers share that session. The session is removed once the
    response leaves this middleware iff we set the scope here.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Always generate the request id server-side. Reading it from an
        # incoming `X-Request-Id` header would let a caller pin every
        # concurrent request to the same SQLAlchemy session-scope key,
        # which collapses to a shared session and corrupts Session state.
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id

        owns_scope = _session_scope.get() == "__default__"
        token = _session_scope.set(request_id) if owns_scope else None
        try:
            response = await call_next(request)
        finally:
            if owns_scope:
                try:
                    db.remove()
                except Exception:
                    pass
                if token is not None:
                    try:
                        _session_scope.reset(token)
                    except ValueError:
                        # scoped_session ran on a copied context (FastAPI's
                        # threadpool); the original token isn't valid here.
                        _session_scope.set("__default__")
        response.headers["X-Request-Id"] = request_id
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        ctx = RequestContext(
            request_id=getattr(request.state, "request_id", uuid.uuid4().hex),
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
        token = set_request_context(ctx)
        try:
            return await call_next(request)
        finally:
            reset_request_context(token)


class RequireAuthMiddleware(BaseHTTPMiddleware):
    """Enforce authentication on every request except a small allowlist.

    Allowlist (from `api.auth.dependencies.AUTH_ALLOWLIST_PREFIXES`):
      - `/api/healthz` (health check, intentionally unauthenticated)
      - `/api/oidc/*` (login flow has to be reachable without a session)

    Static assets (the React SPA mount at `/`) and the docs endpoints
    (`/api/docs`, `/api/openapi.json`) are intentionally **inside** the
    gate. Middleware runs on every ASGI request, including the static
    files mount that bypasses FastAPI's dependency chain — that's the
    point of doing this in middleware rather than as a route dependency.

    On failure, OIDC mode redirects to `/api/oidc/login?next=…`; CF
    Access / dev modes return the same 403 the dependency would have.
    """

    _logger = logging.getLogger(__name__)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        from api.auth.dependencies import OIDCRedirectRequired, get_current_user_id, is_auth_allowlisted

        path = request.url.path
        if is_auth_allowlisted(path):
            return await call_next(request)

        try:
            # `db.session` returns the request-scoped Session set up by
            # RequestIdMiddleware; pass it directly (the dependency
            # signature accepts a bare Session).
            get_current_user_id(request, db.session)
        except OIDCRedirectRequired as exc:
            query = urlencode({"next": exc.next_path})
            return RedirectResponse(url=f"/api/oidc/login?{query}", status_code=307)
        except HTTPException as exc:
            detail: Any = exc.detail
            body: dict[str, Any]
            if isinstance(detail, dict):
                body = detail
            else:
                body = {"message": str(detail) if detail is not None else ""}
            return JSONResponse(body, status_code=exc.status_code, headers=exc.headers or None)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        csp = DEBUG_CSP if settings.DEBUG else CSP
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api") and not path.startswith("/api/swagger-ui") and not path.startswith("/api/docs"):
            response.headers["X-XSS-Protection"] = "0"
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
