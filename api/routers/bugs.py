"""Sentry bug-report proxy.

The React frontend's Sentry DSN is occasionally blocked by ad-blockers, so
the SDK is configured to tunnel envelopes through this endpoint. We rewrite
the placeholder DSN inside the envelope and forward it to the real Sentry
project. See:

  https://docs.sentry.io/platforms/javascript/troubleshooting/#dealing-with-ad-blockers
  https://github.com/getsentry/examples/blob/master/tunneling/python/app.py
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from api.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bugs", tags=["bugs"])

_PLACEHOLDER_DSN = "https://user@example.ingest.sentry.io/1234567"


@router.post("/sentry", name="sentry_bug")
async def post_sentry(request: Request) -> dict[str, Any]:
    """Forwards a Sentry envelope to the configured DSN's tunnel endpoint."""
    if settings.ENV in ("development", "test") or not settings.REACT_SENTRY_DSN:
        return {}

    dsn = urlparse(settings.REACT_SENTRY_DSN)
    if not dsn.hostname:
        raise HTTPException(500, "REACT_SENTRY_DSN is malformed")
    project_id = dsn.path.strip("/")

    envelope = await request.body()
    rewritten = envelope.decode("utf-8").replace(_PLACEHOLDER_DSN, settings.REACT_SENTRY_DSN)

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://{dsn.hostname}/api/{project_id}/envelope/",
                content=rewritten,
                headers={"Content-Type": "application/x-sentry-envelope"},
                timeout=10.0,
            )
    except httpx.HTTPError:
        # Best-effort forwarding — if Sentry is down we don't want the user's
        # bug report to surface a 5xx in the React UI. Log and swallow.
        logger.exception("Sentry envelope forwarding failed")

    return {}
