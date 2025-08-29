import logging
import random
import string
from typing import Any, Optional, TypedDict

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from api_v2.models import OktaUser, Tag
from api_v2.schemas import AuditEventType, AuditLogRead, AuditTagSummary

logger = logging.getLogger(__name__)


class TagDict(TypedDict):
    name: str
    description: str
    constraints: dict[str, Any]


class CreateTag:
    def __init__(
        self,
        db: Session,
        *,
        tag: Tag | TagDict,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        id = self._generate_id()
        if isinstance(tag, dict):
            self.tag = Tag(id=id, name=tag["name"], description=tag["description"], constraints=tag["constraints"])
        else:
            tag.id = id
            self.tag = tag

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def _log_audit_event(self) -> None:
        """Log audit event for tag creation."""
        email = None
        if self.current_user_id is not None:
            email = getattr(self.db.get(OktaUser, self.current_user_id), "email", None)

        # Build audit data
        audit_data = {
            "event_type": AuditEventType.TAG_CREATE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "tag": AuditTagSummary(
                id=self.tag.id, name=self.tag.name, description=self.tag.description, enabled=self.tag.enabled
            ),
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For")
                or self.request.headers.get("X-Real-IP")
                or self.request.client.host
                if self.request.client
                else None
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self) -> Tag:
        # Do not allow non-deleted tags with the same name (case-insensitive)
        existing_tag = (
            self.db.query(Tag)
            .filter(func.lower(Tag.name) == func.lower(self.tag.name))
            .filter(Tag.deleted_at.is_(None))
            .first()
        )
        if existing_tag is not None:
            return existing_tag

        self.db.add(self.tag)
        self.db.commit()

        # Audit logging
        self._log_audit_event()

        return self.tag

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def _generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
