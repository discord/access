from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from api.extensions import db
from api.models import AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api.plugins import get_audit_events_hook
from api.plugins.audit_events import AuditEventEnvelope
from api.views.schemas import AuditLogSchema, EventType
from flask import current_app, has_request_context, request


class DeleteTag:
    def __init__(self, *, tag: Tag | str, current_user_id: Optional[str] = None):
        self.tag = Tag.query.filter(Tag.id == (tag if isinstance(tag, str) else tag.id)).first()
        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> None:
        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.tag_delete,
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

        # Disable and delete tag
        self.tag.enabled = False
        self.tag.deleted_at = db.func.now()

        # End all active group tag mappings for tag
        OktaGroupTagMap.query.filter(
            db.or_(
                OktaGroupTagMap.ended_at.is_(None),
                OktaGroupTagMap.ended_at > db.func.now(),
            )
        ).filter(OktaGroupTagMap.tag_id == self.tag.id).update(
            {OktaGroupTagMap.ended_at: db.func.now()}, synchronize_session="fetch"
        )

        # End all active app tag mappings for tag
        AppTagMap.query.filter(
            db.or_(
                AppTagMap.ended_at.is_(None),
                AppTagMap.ended_at > db.func.now(),
            )
        ).filter(AppTagMap.tag_id == self.tag.id).update(
            {AppTagMap.ended_at: db.func.now()}, synchronize_session="fetch"
        )

        db.session.commit()

        # Emit audit event to plugins (after DB commit)
        try:
            audit_hook = get_audit_events_hook()
            envelope = AuditEventEnvelope(
                id=uuid4(),
                event_type="tag_delete",
                timestamp=datetime.now(timezone.utc),
                actor_id=self.current_user_id or "system",
                actor_email=email,
                target_type="tag",
                target_id=str(self.tag.id),
                target_name=self.tag.name,
                action="deleted",
                reason="",
                payload={
                    "tag_id": str(self.tag.id),
                    "tag_name": self.tag.name,
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
