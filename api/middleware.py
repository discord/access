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

import logging
import secrets
import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.config import settings
from api.context import RequestContext, reset_request_context, set_request_context
from api.extensions import _session_scope, db
from api.plugins._async_dispatch import run_hooks_to_completion
from api.plugins.metrics_reporter import get_metrics_reporter_hook


def build_csp(nonce: str) -> str:
    """Production CSP for the SPA.

    `script-src`/`style-src` allow `'self'` plus a per-response nonce (see
    `SecurityHeadersMiddleware`, threaded into the served `index.html` by
    `api.app.serve_spa`): the nonce authorizes the inline bootstrap `<script>`
    and styled-components' runtime `<style>` injections, while same-origin
    bundles/stylesheets are covered by `'self'` and Google Fonts by its host
    allowance.

    Two classes of *benign* console warnings are expected under this policy and
    are intentionally not accommodated — silencing either would weaken the
    policy for no functional gain:

    - `style-src` violations from Sentry Session Replay (rrweb). rrweb diffs
      style changes by calling `setAttribute("style", ...)` on detached nodes.
      A nonce cannot authorize a `style=""` *attribute* (nonces only cover
      `<style>`/`<script>` *elements*), and `'unsafe-inline'` is ignored by the
      browser whenever a nonce is present. The writes are on nodes that never
      render, so there is no visual impact. The app's own inline styles are
      unaffected because React/MUI set them via CSSOM (`element.style.x = ...`),
      which CSP does not govern — only the string `setAttribute` form is.
    - a single `script-src` `eval` violation from `@mui/x-data-grid`, which
      feature-detects `eval` inside a `try/catch` to pick a fast filter path.
      The probe is caught and the grid falls back to an interpreted path;
      allowing it would require `'unsafe-eval'`.
    """
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' *.ingest.sentry.io; "
        "worker-src 'self' blob:; "
        "frame-ancestors 'none'"
    )


# Relaxed CSP for development and the (optional) API docs: FastAPI's
# auto-generated `/api/docs` page loads Swagger UI assets from a CDN and
# bootstraps with an inline `<script>` that can't carry our per-response nonce,
# so those paths keep `'unsafe-inline'` + the CDN allowance.
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
                    await db.remove()
                except Exception:
                    pass
                if token is not None:
                    try:
                        _session_scope.reset(token)
                    except ValueError:
                        # the scope was set on a copied context; the original
                        # token isn't valid here.
                        _session_scope.set("__default__")
        response.headers["X-Request-Id"] = request_id
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        ctx = RequestContext(
            request_id=getattr(request.state, "request_id", uuid.uuid4().hex),
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
            source="web",
        )
        token = set_request_context(ctx)
        try:
            return await call_next(request)
        finally:
            reset_request_context(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    # Docs endpoints whose inline Swagger bootstrap / CDN assets can't carry the
    # per-response nonce; they keep the relaxed `DEBUG_CSP` even in production
    # (API docs are optionally exposed there — see `settings.expose_api_docs`).
    _DOCS_PATHS = ("/api/docs", "/api/openapi.json")

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Generate the CSP nonce before the route runs so the SPA catch-all
        # (`api.app.serve_spa`) can stamp the *same* value into the served
        # `index.html` (and `window.__webpack_nonce__`) that we emit in the
        # header below.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", self._csp_for(request, nonce))
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if settings.ENV != "development":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    def _csp_for(self, request: Request, nonce: str) -> str:
        path = request.url.path
        if settings.DEBUG or path in self._DOCS_PATHS or path.startswith("/api/swagger-ui"):
            return DEBUG_CSP
        return build_csp(nonce)


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        path = request.url.path
        is_api = path.startswith("/api") and not path.startswith("/api/swagger-ui") and not path.startswith("/api/docs")
        # MCP responses are JSON-RPC over Streamable HTTP — never cacheable.
        is_mcp = path == "/mcp" or path.startswith("/mcp/")
        if is_api or is_mcp:
            response.headers["X-XSS-Protection"] = "0"
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


class RequestObservabilityMiddleware:
    """Emit per-request counter and duration histogram via the metrics_reporter hook."""

    _logger = logging.getLogger(__name__)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def _send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            await self._record(scope.get("method", ""), status_code, duration_ms)

    async def _record(self, method: str, status_code: int, duration_ms: float) -> None:
        try:
            hook = get_metrics_reporter_hook()
        except Exception:
            self._logger.exception("metrics_reporter hook unavailable; skipping emit")
            return
        tags = {"method": method, "status": str(status_code)}
        try:
            # run_hooks_to_completion uses asyncio.wait (not gather) and logs any
            # per-plugin failure itself; emit is best-effort and never fails the request.
            await run_hooks_to_completion(
                hook.record_counter(metric_name="requests", value=1, tags=tags),
                context="metrics requests counter",
            )
            await run_hooks_to_completion(
                hook.record_histogram(metric_name="request.duration", value=duration_ms, tags={**tags, "unit": "ms"}),
                context="metrics request.duration histogram",
            )
        except Exception:
            self._logger.exception("metrics_reporter emit failed; continuing")
