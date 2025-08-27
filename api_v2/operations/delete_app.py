"""
Delete app operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api_v2.models import App, AppGroup, AppTagMap, OktaUser
from api_v2.operations.delete_group import DeleteGroup
from api_v2.schemas import AuditEventType, AuditLogRead, AuditAppSummary

logger = logging.getLogger(__name__)


class DeleteApp:
    def __init__(
        self,
        db: Session,
        *,
        app: App | str,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        if isinstance(app, str):
            self.app = (
                self.db.query(App)
                .filter(App.deleted_at.is_(None))
                .filter(App.id == app)
                .first()
            )
        else:
            self.app = app

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def _log_audit_event(self) -> None:
        """Log the app deletion audit event."""
        email = None
        if self.current_user_id is not None:
            user = self.db.get(OktaUser, self.current_user_id)
            email = getattr(user, "email", None)

        audit_data = {
            "event_type": AuditEventType.APP_DELETE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "app": AuditAppSummary(
                id=self.app.id,
                name=self.app.name,
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
        # Prevent access app deletion
        if self.app.name == App.ACCESS_APP_RESERVED_NAME:
            raise ValueError("The Access Application cannot be deleted")

        # Audit logging
        self._log_audit_event()

        self.app.deleted_at = func.now()
        self.db.commit()

        # Delete all associated Okta App Groups and end their membership
        app_groups = (
            self.db.query(AppGroup)
            .filter(AppGroup.deleted_at.is_(None))
            .filter(AppGroup.app_id == self.app.id)
        )
        app_group_ids = [ag.id for ag in app_groups]
        for app_group_id in app_group_ids:
            DeleteGroup(
                self.db,
                group=app_group_id,
                current_user_id=self.current_user_id,
                request=self.request,
            ).execute()

        # End all tag mappings for this app (OktaGroupTagMaps are ended by the DeleteGroup operation above)
        self.db.query(AppTagMap).filter(AppTagMap.app_id == self.app.id).filter(
            or_(
                AppTagMap.ended_at.is_(None),
                AppTagMap.ended_at > func.now(),
            )
        ).update(
            {AppTagMap.ended_at: func.now()},
            synchronize_session="fetch",
        )
        self.db.commit()