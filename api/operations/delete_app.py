from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaUser
from api.operations.delete_group import DeleteGroup
from api.plugins import get_audit_events_hook
from api.plugins.audit_events import AuditEventEnvelope
from api.views.schemas import AuditLogSchema, EventType
from flask import current_app, has_request_context, request


class DeleteApp:
    def __init__(self, *, app: App | str, current_user_id: Optional[str] = None):
        if isinstance(app, str):
            self.app = App.query.filter(App.deleted_at.is_(None)).filter(App.id == app).first()
        else:
            self.app = app

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> None:
        # Prevent access app deletion
        if self.app.name == App.ACCESS_APP_RESERVED_NAME:
            raise ValueError("The Access Application cannot be deleted")

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_delete,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "app": self.app,
                }
            )
        )

        # Store app details before deletion for audit event
        app_id = self.app.id
        app_name = self.app.name

        self.app.deleted_at = db.func.now()
        db.session.commit()

        # Emit audit event to plugins (after DB commit)
        try:
            audit_hook = get_audit_events_hook()
            envelope = AuditEventEnvelope(
                id=uuid4(),
                event_type="app_delete",
                timestamp=datetime.now(timezone.utc),
                actor_id=self.current_user_id or "system",
                actor_email=email,
                target_type="app",
                target_id=str(app_id),
                target_name=app_name,
                action="deleted",
                reason="",
                payload={
                    "app_id": str(app_id),
                    "app_name": app_name,
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

        # Delete all associated Okta App Groups and end their membership
        app_groups = AppGroup.query.filter(AppGroup.deleted_at.is_(None)).filter(AppGroup.app_id == self.app.id)
        app_group_ids = [ag.id for ag in app_groups]
        for app_group_id in app_group_ids:
            DeleteGroup(group=app_group_id, current_user_id=self.current_user_id).execute()

        # End all tag mappings for this app (OktaGroupTagMaps are ended by the DeleteGroup operation above)
        AppTagMap.query.filter(AppTagMap.app_id == self.app.id).filter(
            db.or_(
                AppTagMap.ended_at.is_(None),
                AppTagMap.ended_at > db.func.now(),
            )
        ).update(
            {AppTagMap.ended_at: db.func.now()},
            synchronize_session="fetch",
        )
        db.session.commit()
