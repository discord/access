"""Health check router. Public — no auth required."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.database import DbSession

router = APIRouter(prefix="/api/healthz", tags=["health"])


@router.get("", name="health_check")
def health_check(db: DbSession) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    return JSONResponse({"status": "ok"}, status_code=200)
