import random
import string
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict
from uuid import uuid4

from api.extensions import db
from api.models import OktaUser, Tag
from api.plugins import get_audit_events_hook
from api.plugins.audit_events import AuditEventEnvelope
from api.views.schemas import AuditLogSchema, EventType
from flask import current_app, has_request_context, request
from sqlalchemy import func


class TagDict(TypedDict):
    name: str
    description: str
    constraints: dict[str, Any]


class CreateTag:
    def __init__(self, *, tag: Tag | TagDict, current_user_id: Optional[str] = None):
        id = self.__generate_id()
        if isinstance(tag, dict):
            self.tag = Tag(id=id, name=tag["name"], description=tag["description"], constraints=tag["constraints"])
        else:
            tag.id = id
            self.tag = tag

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> Tag:
        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_tag = (
            Tag.query.filter(func.lower(Tag.name) == func.lower(self.tag.name)).filter(Tag.deleted_at.is_(None)).first()
        )
        if existing_tag is not None:
            return existing_tag

        db.session.add(self.tag)
        db.session.commit()

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.tag_create,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "tag": self.tag,
                }
            )
        )

        # Emit audit event to plugins (after DB commit)
        try:
            audit_hook = get_audit_events_hook()
            envelope = AuditEventEnvelope(
                id=uuid4(),
                event_type="tag_create",
                timestamp=datetime.now(timezone.utc),
                actor_id=self.current_user_id or "system",
                actor_email=email,
                target_type="tag",
                target_id=str(self.tag.id),
                target_name=self.tag.name,
                action="created",
                reason="",
                payload={
                    "tag_id": str(self.tag.id),
                    "tag_name": self.tag.name,
                    "tag_description": self.tag.description,
                },
                metadata={
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip_address": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                },
            )
            audit_hook.audit_event_logged(envelope=envelope)
        except Exception as e:
            current_app.logger.error(f"Failed to emit audit event: {e}", exc_info=True)

        return self.tag

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
