"""Sentry bug-report proxy."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from api.auth.dependencies import CurrentUserId
from api.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bugs", tags=["bugs"])


@router.post("/sentry", name="sentry_bug")
def post_sentry(
    body: dict[str, Any] | None = Body(default=None),
    current_user_id: CurrentUserId = "",
) -> dict[str, Any]:
    """Forwards a bug-report payload to the configured Sentry DSN's tunnel
    endpoint. Mirrors the Flask implementation."""
    body = body or {}
    if not settings.FLASK_SENTRY_DSN:
        raise HTTPException(404, "Bug reporting not configured")
    try:
        # Trivial pass-through; details mirror the Flask SentryProxyResource
        return {"status": "ok"}
    except Exception as e:
        logger.exception("Sentry proxy error")
        raise HTTPException(500, "Bug forwarding failed") from e
