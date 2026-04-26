"""Okta webhook endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header

from api.config import settings
from api.database import DbSession

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/okta", name="okta_webhook")
def okta_webhook(
    body: dict[str, Any] = Body(...),
    db: DbSession = None,  # type: ignore[assignment]
    x_okta_verification_challenge: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receives Okta event hook deliveries. Currently a stub that
    acknowledges the verification challenge and otherwise no-ops."""
    if x_okta_verification_challenge:
        return {"verification": x_okta_verification_challenge}
    return {"received": True}
