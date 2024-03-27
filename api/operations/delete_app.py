from typing import Optional

from flask import current_app, has_request_context, request

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaUser
from api.operations.delete_group import DeleteGroup
from api.views.schemas import AuditLogSchema, EventType


class DeleteApp:
    def __init__(
        self,
         *,
         app:  App | str,
         current_user_id: Optional[str] = None
    ):
        if isinstance(app, str):
            self.app = (
                App.query.filter(App.deleted_at.is_(None))
                .filter(App.id == app)
                .first()
            )
        else:
            self.app = app

        self.current_user_id = (
            getattr(OktaUser.query
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id).first(), 'id', None)
        )

    def execute(self) -> None:
        # Prevent access app deletion
        if self.app.name == App.ACCESS_APP_RESERVED_NAME:
            raise ValueError("The Access Application cannot be deleted")

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), 'email', None)

        context = has_request_context()

        current_app.logger.info(AuditLogSchema().dumps({
            'event_type' : EventType.app_delete,
            'user_agent' : request.headers.get('User-Agent') if context else None,
            'ip' : request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
                        if context else None,
            'current_user_id' : self.current_user_id,
            'current_user_email' : email,
            'app' : self.app
        }))

        self.app.deleted_at = db.func.now()
        db.session.commit()

        # Delete all associated Okta App Groups and end their membership
        app_groups = AppGroup.query.filter(AppGroup.deleted_at.is_(None)).filter(
            AppGroup.app_id == self.app.id
        )
        app_group_ids = [ag.id for ag in app_groups]
        for app_group_id in app_group_ids:
            DeleteGroup(group=app_group_id, current_user_id=self.current_user_id).execute()

        # End all tag mappings for this app (OktaGroupTagMaps are ended by the DeleteGroup operation above)
        AppTagMap.query.filter(
            AppTagMap.app_id == self.app.id
        ).filter(
            db.or_(
                AppTagMap.ended_at.is_(None),
                AppTagMap.ended_at > db.func.now(),
            )
        ).update(
            {AppTagMap.ended_at: db.func.now()},
            synchronize_session="fetch",
        )
        db.session.commit()