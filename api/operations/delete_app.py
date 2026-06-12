from typing import Optional

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaUser
from api.operations.delete_group import DeleteGroup
from api.schemas import AuditLogSchema, EventType


class DeleteApp:
    def __init__(self, *, app: App | str, current_user_id: Optional[str] = None):
        self._app_arg = app
        self._current_user_id_arg = current_user_id

    async def _resolve(self) -> None:
        app = self._app_arg
        if isinstance(app, str):
            app_result = await db.session.scalars(select(App).where(App.deleted_at.is_(None)).where(App.id == app))
            self.app = app_result.first()
        else:
            self.app = app

        self.current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser)
                    .where(OktaUser.deleted_at.is_(None))
                    .where(OktaUser.id == self._current_user_id_arg)
                )
            ).first(),
            "id",
            None,
        )

    async def execute(self) -> None:
        await self._resolve()
        # Prevent access app deletion
        if self.app.name == App.ACCESS_APP_RESERVED_NAME:
            raise ValueError("The Access Application cannot be deleted")

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, self.current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_delete,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "app": self.app,
                }
            )
        )

        self.app.deleted_at = func.now()
        await db.session.commit()

        # Delete all associated Okta App Groups and end their membership
        app_groups = (
            await db.session.scalars(
                select(AppGroup).where(AppGroup.deleted_at.is_(None)).where(AppGroup.app_id == self.app.id)
            )
        ).all()
        app_group_ids = [ag.id for ag in app_groups]
        for app_group_id in app_group_ids:
            await DeleteGroup(group=app_group_id, current_user_id=self.current_user_id).execute()

        # End all tag mappings for this app (OktaGroupTagMaps are ended by the DeleteGroup operation above)
        await db.session.execute(
            update(AppTagMap)
            .where(AppTagMap.app_id == self.app.id)
            .where(
                or_(
                    AppTagMap.ended_at.is_(None),
                    AppTagMap.ended_at > func.now(),
                )
            )
            .values({AppTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )
        await db.session.commit()
