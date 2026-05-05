"""FastAPI exception handlers — emit the `{"message": ...}` envelope used
by the React frontend. FastAPI's default is `{"detail": ...}`; we override
across `HTTPException`, `RequestValidationError`, and the catch-all
`Exception` handler so the wire shape stays consistent. Adopting the
RFC 9457 problem-detail format is a follow-up (POST_MIGRATION_TODO #7)."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).resolve().parent.parent / "build" / "index.html"


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/") or request.url.path == "/api"


def _format_validation_error(exc: RequestValidationError | ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Validation error"
    err = errors[0]
    loc = err.get("loc")
    msg = err.get("msg", "Invalid input")
    if loc:
        return f"{'.'.join(str(x) for x in loc[1:] if x is not None) or 'body'}: {msg}"
    return str(msg)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse | HTMLResponse:
    detail = exc.detail
    # Cloudflare and other 404s for non-/api/ paths should serve the SPA.
    if exc.status_code == 404 and not _is_api(request):
        if INDEX_HTML.exists():
            return HTMLResponse(INDEX_HTML.read_text(), status_code=200)
        return JSONResponse({"message": "Not Found"}, status_code=404)
    if isinstance(detail, (str, type(None))):
        body: dict[str, Any] = {"message": detail or ""}
    elif isinstance(detail, dict):
        body = detail
    else:
        body = {"message": str(detail)}
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers or None)


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"message": _format_validation_error(exc)}, status_code=400)


async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse({"message": _format_validation_error(exc)}, status_code=400)


async def oidc_redirect_handler(request: Request, exc: OIDCRedirectRequired) -> RedirectResponse:
    query = urlencode({"next": exc.next_path})
    return RedirectResponse(url=f"/api/oidc/login?{query}", status_code=307)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse | HTMLResponse:
    logger.exception("Unhandled exception", exc_info=exc)
    if _is_api(request):
        # Return a static body — `str(exc)` for SQLAlchemy errors leaks the
        # full SQL statement, table/column names, and bound parameters.
        # Diagnostics already go to the log pipeline via logger.exception.
        return JSONResponse({"message": "Internal Server Error"}, status_code=500)
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(), status_code=200)
    return JSONResponse({"message": "Internal Server Error"}, status_code=500)


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, pydantic_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(OIDCRedirectRequired, oidc_redirect_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
