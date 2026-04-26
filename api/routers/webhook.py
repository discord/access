"""Okta event-hook endpoint.

Authentication is gated on `settings.OKTA_WEBHOOK_ID` matching the caller's
resolved user id (Okta is configured with a service token whose user id is
this value). All other callers receive 403.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy.orm import joinedload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.config import settings
from api.database import DbSession
from api.extensions import db as _db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import ModifyGroupUsers

OKTA_WEBHOOK_VERIFICATION_HEADER_NAME = "X-Okta-Verification-Challenge"
OKTA_IGA_USER_AGENT = "okta_iga_connector"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _check_webhook_caller(current_user_id: str) -> None:
    if settings.OKTA_WEBHOOK_ID is None or current_user_id != settings.OKTA_WEBHOOK_ID:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/okta", name="okta_webhook")
def okta_webhook_get(
    request: Request,
    current_user_id: CurrentUserId,
    db: DbSession,
) -> dict[str, Any]:
    """One-time Okta verification challenge handler."""
    _check_webhook_caller(current_user_id)
    challenge = request.headers.get(OKTA_WEBHOOK_VERIFICATION_HEADER_NAME)
    if challenge is None:
        logger.error("Okta event webhook verification request is missing header")
        raise HTTPException(400, "Missing verification challenge header")
    return {"verification": challenge}


@router.post("/okta", name="okta_webhook_post")
def okta_webhook_post(
    request: Request,
    current_user_id: CurrentUserId,
    db: DbSession,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Receives Okta event-hook deliveries (group membership changes from
    the IGA Access Certification Campaigns)."""
    _check_webhook_caller(current_user_id)
    if body is None:
        logger.error("Okta event webhook request is not JSON")
        raise HTTPException(400, "Request body must be JSON")

    events = (body.get("data") or {}).get("events", [])

    for event in events:
        user_agent = ((event.get("client") or {}).get("userAgent") or {}).get("rawUserAgent")
        if user_agent != OKTA_IGA_USER_AGENT:
            logger.info("Okta webhook event is not from Okta IGA user agent: %s", user_agent)
            continue

        actor_id = (event.get("actor") or {}).get("id")
        if settings.OKTA_IGA_ACTOR_ID is None or actor_id != settings.OKTA_IGA_ACTOR_ID:
            logger.warning("Okta webhook event is not from configured IGA actor: %s", actor_id)
            continue

        event_type = event.get("eventType", "")
        if event_type not in ("group.user_membership.add", "group.user_membership.remove"):
            logger.warning("Okta webhook event type is unexpected: %s", event_type)
            continue

        user = None
        group = None
        targets = event.get("target", [])
        for target in targets:
            if target.get("type") == "User":
                user = (
                    db.query(OktaUser)
                    .filter(OktaUser.id == target.get("id"))
                    .filter(OktaUser.deleted_at.is_(None))
                    .first()
                )
            elif target.get("type") == "UserGroup":
                group = (
                    db.query(OktaGroup)
                    .filter(OktaGroup.id == target.get("id"))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .filter(OktaGroup.is_managed.is_(True))
                    .first()
                )

        if user is None or group is None:
            logger.warning("Could not find active user or group in target: %s", json.dumps(targets))
            continue

        if event_type == "group.user_membership.add":
            ModifyGroupUsers(
                group=group,
                current_user_id=current_user_id,
                members_to_add=[user.id],
            ).execute()
        elif event_type == "group.user_membership.remove":
            ModifyGroupUsers(
                group=group,
                current_user_id=current_user_id,
                members_to_remove=[user.id],
            ).execute()

        # If the user is in this group via a role, remove them from the role too
        if type(group) is not RoleGroup and event_type == "group.user_membership.remove":
            active_role_memberships = (
                db.query(OktaUserGroupMember)
                .options(
                    joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
                        RoleGroupMap.active_role_group
                    )
                )
                .filter(OktaUserGroupMember.user_id == user.id)
                .filter(OktaUserGroupMember.group_id == group.id)
                .filter(
                    _db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > _db.func.now(),
                    )
                )
                .filter(OktaUserGroupMember.is_owner.is_(False))
                .filter(OktaUserGroupMember.role_group_map_id.is_not(None))
                .all()
            )
            for membership in active_role_memberships:
                role_mapping = membership.active_role_group_mapping
                if role_mapping is not None and role_mapping.active_role_group is not None:
                    if role_mapping.active_role_group.is_managed:
                        ModifyGroupUsers(
                            group=role_mapping.role_group_id,
                            current_user_id=current_user_id,
                            members_to_remove=[user.id],
                        ).execute()

    return {"accepted": True}
