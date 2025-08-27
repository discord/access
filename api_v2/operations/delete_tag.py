"""
Delete tag operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api_v2.models import AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api_v2.schemas import AuditEventType, AuditLogRead, AuditTagSummary

logger = logging.getLogger(__name__)


class DeleteTag:
    def __init__(
        self,
        db: Session,
        *,
        tag: Tag | str,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        self.tag = (
            self.db.query(Tag)
            .filter(Tag.id == (tag if isinstance(tag, str) else tag.id))
            .first()
        )

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def _log_audit_event(self) -> None:
        """Log the tag deletion audit event."""
        email = None
        if self.current_user_id is not None:
            user = self.db.get(OktaUser, self.current_user_id)
            email = getattr(user, "email", None)

        audit_data = {
            "event_type": AuditEventType.TAG_DELETE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "tag": AuditTagSummary(
                id=self.tag.id,
                name=self.tag.name,
                description=self.tag.description or "",
                enabled=self.tag.enabled,
            ),
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For")
                or self.request.headers.get("X-Real-IP")
                or (self.request.client.host if self.request.client else None)
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self) -> None:
        # Audit logging
        self._log_audit_event()

        # Disable and delete tag
        self.tag.enabled = False
        self.tag.deleted_at = func.now()

        # End all active group tag mappings for tag
        self.db.query(OktaGroupTagMap).filter(
            or_(
                OktaGroupTagMap.ended_at.is_(None),
                OktaGroupTagMap.ended_at > func.now(),
            )
        ).filter(OktaGroupTagMap.tag_id == self.tag.id).update(
            {OktaGroupTagMap.ended_at: func.now()}, synchronize_session="fetch"
        )

        # End all active app tag mappings for tag
        self.db.query(AppTagMap).filter(
            or_(
                AppTagMap.ended_at.is_(None),
                AppTagMap.ended_at > func.now(),
            )
        ).filter(AppTagMap.tag_id == self.tag.id).update(
            {AppTagMap.ended_at: func.now()}, synchronize_session="fetch"
        )

        self.db.commit()