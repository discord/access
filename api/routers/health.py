"""Health check router. Public — no auth required."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.database import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/healthz", tags=["health"])


# Returns a `JSONResponse` directly because the success and failure paths
# emit different JSON shapes (`{"status":"ok"}` vs `{"status":"error"}`)
# at different status codes. FastAPI's `response_model` filtering is silently
# skipped when the handler returns a `Response` subclass, so we don't declare
# one here — adding it would be misleading. Refactoring to a single
# `HealthResponse` would change the error-path wire shape.
@router.get("", name="health_check")
def health_check(db: DbSession) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        # Log server-side only — the exception text carries driver/connection
        # detail (host, db name, user) and this endpoint is unauthenticated.
        logger.exception("Health check DB query failed")
        return JSONResponse({"status": "error"}, status_code=500)
    return JSONResponse({"status": "ok"}, status_code=200)
