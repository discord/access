from typing import Optional

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context

from api.extensions import db
from api.models import AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api.schemas import AuditLogSchema, EventType


class DeleteTag:
    def __init__(self, *, tag: Tag | str, current_user_id: Optional[str] = None):
        self.tag_id = tag if isinstance(tag, str) else tag.id
        self.current_user_id = current_user_id

    async def execute(self) -> None:
        tag = (await db.session.scalars(select(Tag).where(Tag.id == self.tag_id))).first()
        assert tag is not None
        current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first(),
            "id",
            None,
        )

        # Audit logging
        email = None
        if current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.tag_delete,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": current_user_id,
                    "current_user_email": email,
                    "tag": tag,
                }
            )
        )

        # Disable and delete tag
        tag.enabled = False
        tag.deleted_at = func.now()

        # End all active group tag mappings for tag
        await db.session.execute(
            update(OktaGroupTagMap)
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
            .where(OktaGroupTagMap.tag_id == tag.id)
            .values({OktaGroupTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        # End all active app tag mappings for tag
        await db.session.execute(
            update(AppTagMap)
            .where(
                or_(
                    AppTagMap.ended_at.is_(None),
                    AppTagMap.ended_at > func.now(),
                )
            )
            .where(AppTagMap.tag_id == tag.id)
            .values({AppTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        await db.session.commit()
