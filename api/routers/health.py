"""Health check router. Public — no auth required."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.database import DbSession

router = APIRouter(prefix="/api/healthz", tags=["health"])


# Returns a `JSONResponse` directly because the success and failure paths
# emit different JSON shapes (`{"status":"ok"}` vs `{"status":"error","error":"..."}`)
# at different status codes. FastAPI's `response_model` filtering is silently
# skipped when the handler returns a `Response` subclass, so we don't declare
# one here — adding it would be misleading. Refactoring to a single
# `HealthResponse` would change the error-path wire shape.
@router.get("", name="health_check")
def health_check(db: DbSession) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    return JSONResponse({"status": "ok"}, status_code=200)
