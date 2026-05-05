"""FastAPI dependencies for resolving the authenticated user.

`get_current_user_id` returns the OktaUser id of the caller. It supports:

  - Dev/test bypass: read `app.state.current_user_email` (or fall back to
    `settings.CURRENT_OKTA_USER_EMAIL`) and look up the user by email.
  - Cloudflare Access JWT: verify the JWT and resolve to a user by email
    (human) or `common_name` (service token).
  - OIDC session: read `request.session["userinfo"]["email"]`.

Tests typically override these via `app.dependency_overrides`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException
from sentry_sdk import set_user
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.requests import Request

from api.auth.cloudflare import extract_token, verify_cloudflare_token
from api.config import settings
from api.database import DbSession
from api.models import OktaUser

logger = logging.getLogger(__name__)


def _lookup_user_by_email(db: Session, email: str) -> OktaUser:
    user = (
        db.query(OktaUser)
        .filter(func.lower(OktaUser.email) == func.lower(email))
        .filter(OktaUser.deleted_at.is_(None))
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _dev_user_email(request: Request) -> str:
    return getattr(request.app.state, "current_user_email", None) or settings.CURRENT_OKTA_USER_EMAIL


def get_current_user_id(request: Request, db: DbSession) -> str:
    """Resolve the current user id, raising 403 if unauthenticated."""
    if settings.ENV in ("development", "test"):
        email = _dev_user_email(request)
        if email == "Unauthenticated":
            # Health-check tests set this sentinel to opt out of having a
            # real OktaUser row resolved; the endpoint runs without auth.
            return ""
        user = _lookup_user_by_email(db, email)
        request.state.current_user_id = user.id
        if settings.FLASK_SENTRY_DSN:
            set_user({"id": user.id})
        return user.id

    if settings.CLOUDFLARE_TEAM_DOMAIN:
        token = extract_token(request)
        if not token:
            raise HTTPException(status_code=403, detail="Missing required Cloudflare authorization token")
        payload = verify_cloudflare_token(token)
        if "email" in payload:
            user = _lookup_user_by_email(db, payload["email"])
            request.state.current_user_id = user.id
            if settings.FLASK_SENTRY_DSN:
                set_user({"id": user.id})
            return user.id
        elif "common_name" in payload:
            # Service token: pass `common_name` through as the current_user_id
            # so downstream operations log it; permission checks that need a
            # real OktaUser will fail naturally if no row matches.
            common_name = payload["common_name"]
            request.state.current_user_id = common_name
            return common_name
        raise HTTPException(status_code=403, detail="Invalid Cloudflare authorization token")

    if settings.OIDC_CLIENT_SECRETS:
        userinfo = request.session.get("userinfo") if hasattr(request, "session") else None
        if not userinfo or "email" not in userinfo:
            raise HTTPException(status_code=403, detail="Not logged in")
        user = _lookup_user_by_email(db, userinfo["email"])
        request.state.current_user_id = user.id
        if settings.FLASK_SENTRY_DSN:
            set_user({"id": user.id})
        return user.id

    raise HTTPException(status_code=403, detail="No authentication method configured")


def get_current_user(
    db: DbSession,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
) -> OktaUser:
    user = db.query(OktaUser).filter(OktaUser.id == current_user_id).filter(OktaUser.deleted_at.is_(None)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


CurrentUserId = Annotated[str, Depends(get_current_user_id)]
CurrentUser = Annotated[OktaUser, Depends(get_current_user)]
