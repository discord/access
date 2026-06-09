"""FastAPI exception handlers — emit the RFC 9457 problem-detail envelope.

All HTTP errors cross the wire as
`{"type": "about:blank", "title": "<reason>", "status": <code>, "detail": "<message>"}`
with `Content-Type: application/problem+json`. Validation errors include a
non-standard `errors:` extension with the full FastAPI/Pydantic error list
for clients that want it.

`PluginNotFoundError` is the lone outlier: it emits `{"error": ...}` because
the React plugin-admin page reads `error` (not `detail`). Migrating that page
to RFC 9457 is a separate, frontend-coupled change.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any

from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from api.auth.dependencies import OIDCRedirectRequired
from api.plugins.app_group_lifecycle import PluginNotFoundError

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).resolve().parent.parent / "build" / "index.html"

PROBLEM_JSON = "application/problem+json"


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/") or request.url.path == "/api"


def _problem(
    *,
    status_code: int,
    detail: str,
    title: str | None = None,
    extras: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build a `JSONResponse` with the RFC 9457 problem-detail envelope.

    `type` defaults to `"about:blank"` — we'll grow a typed URI scheme later if
    a specific error class needs it.
    """
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title or HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
    }
    if extras:
        body.update(extras)
    return JSONResponse(
        body,
        status_code=status_code,
        media_type=PROBLEM_JSON,
        headers=headers,
    )


def _format_validation_detail(exc: RequestValidationError | ValidationError) -> str:
    """First-error human-readable summary, kept short for UI surfaces."""
    errors = exc.errors()
    if not errors:
        return "Validation error"
    err = errors[0]
    loc = err.get("loc")
    msg = err.get("msg", "Invalid input")
    if loc:
        return f"{'.'.join(str(x) for x in loc[1:] if x is not None) or 'body'}: {msg}"
    return str(msg)


def _validation_errors(exc: RequestValidationError | ValidationError) -> list[dict[str, Any]]:
    """Sanitize FastAPI/Pydantic's `.errors()` for the wire.

    Drops the bound `input` value because it may include caller data we don't
    want to echo. `ctx` can contain non-JSON-serializable values (e.g. a raw
    `ValueError` instance) — stringify those.
    """
    out: list[dict[str, Any]] = []
    for err in exc.errors():
        clean: dict[str, Any] = {}
        for k, v in err.items():
            if k == "input":
                continue
            if k == "ctx" and isinstance(v, dict):
                clean["ctx"] = {ck: str(cv) for ck, cv in v.items()}
            else:
                clean[k] = v
        out.append(clean)
    return out


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse | HTMLResponse:
    detail = exc.detail
    # Cloudflare and other 404s for non-/api/ paths should serve the SPA.
    if exc.status_code == 404 and not _is_api(request):
        if INDEX_HTML.exists():
            return HTMLResponse(INDEX_HTML.read_text(), status_code=200)
        return _problem(status_code=404, detail="Not Found", headers=exc.headers or None)
    # When the handler raises `HTTPException(detail={...})` it's already
    # supplying a structured body — pass it through as the problem-detail
    # `detail` field instead of stringifying it.
    extras: dict[str, Any] | None = None
    if isinstance(detail, dict):
        detail_str = detail.get("detail") or detail.get("message") or ""
        extras = {k: v for k, v in detail.items() if k not in ("detail", "message")}
        return _problem(
            status_code=exc.status_code,
            detail=str(detail_str),
            extras=extras or None,
            headers=exc.headers or None,
        )
    if detail is None:
        detail_str = ""
    else:
        detail_str = str(detail)
    return _problem(
        status_code=exc.status_code,
        detail=detail_str,
        headers=exc.headers or None,
    )


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _problem(
        status_code=400,
        title="Bad Request",
        detail=_format_validation_detail(exc),
        extras={"errors": _validation_errors(exc)},
    )


async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return _problem(
        status_code=400,
        title="Bad Request",
        detail=_format_validation_detail(exc),
        extras={"errors": _validation_errors(exc)},
    )


async def oidc_redirect_handler(request: Request, exc: OIDCRedirectRequired) -> RedirectResponse:
    query = urlencode({"next": exc.next_path})
    return RedirectResponse(url=f"/oidc/login?{query}", status_code=307)


async def plugin_not_found_handler(request: Request, exc: PluginNotFoundError) -> JSONResponse:
    # The React plugin-admin client reads `error` (not `detail`) from these
    # endpoints, so this handler diverges from the shared RFC 9457 envelope.
    # Migrating it requires a coordinated frontend change — tracked as future
    # follow-up; out of scope for the RFC 9457 conversion.
    return JSONResponse({"error": f"Plugin '{exc.plugin_id}' not found"}, status_code=404)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse | HTMLResponse:
    logger.exception("Unhandled exception", exc_info=exc)
    if _is_api(request):
        # Return a static body — `str(exc)` for SQLAlchemy errors leaks the
        # full SQL statement, table/column names, and bound parameters.
        # Diagnostics already go to the log pipeline via logger.exception.
        return _problem(status_code=500, detail="Internal Server Error")
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(), status_code=200)
    return _problem(status_code=500, detail="Internal Server Error")


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, pydantic_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(OIDCRedirectRequired, oidc_redirect_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PluginNotFoundError, plugin_not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
