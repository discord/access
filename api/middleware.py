"""ASGI middleware for the Access API.

- RequestIdMiddleware: tags each request with a UUID, stored on `request.state`
  and used as the SQLAlchemy session scope key.
- RequestContextMiddleware: builds a `RequestContext` from the request headers
  and binds it to a ContextVar so audit logging in operations can read it.
- SecurityHeadersMiddleware: emits the CSP / X-Frame-Options / Referrer-Policy
  / X-Content-Type-Options headers on every response.
- CacheControlMiddleware: emits no-store cache headers on `/api/*` responses.

Authentication is enforced via the FastAPI app-wide
`dependencies=[Depends(require_authenticated)]` declared in `api.app`,
not via middleware — that keeps `OIDCRedirectRequired` /
`HTTPException` flowing through the registered exception handlers
(see `api.exception_handlers`) instead of duplicating the response
shapes here. Static assets (the SPA) are covered by a catch-all
FastAPI route, which also runs through the app-wide dependency.
"""

from __future__ import annotations

import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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
