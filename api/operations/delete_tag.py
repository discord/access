from typing import Optional

import logging

from sqlalchemy import func, or_, select, update
from api.context import get_request_context

from api.extensions import db
from api.models import AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api.schemas import AuditLogSchema, EventType


class DeleteTag:
    def __init__(self, *, tag: Tag | str, current_user_id: Optional[str] = None):
        self.tag = db.session.scalars(select(Tag).where(Tag.id == (tag if isinstance(tag, str) else tag.id))).first()
        self.current_user_id = getattr(
            db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
            ).first(),
            "id",
            None,
        )

    def execute(self) -> None:
        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.tag_delete,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "tag": self.tag,
                }
            )
        )

        # Disable and delete tag
        self.tag.enabled = False
        self.tag.deleted_at = func.now()

        # End all active group tag mappings for tag
        db.session.execute(
            update(OktaGroupTagMap)
            .where(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            )
            .where(OktaGroupTagMap.tag_id == self.tag.id)
            .values({OktaGroupTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        # End all active app tag mappings for tag
        db.session.execute(
            update(AppTagMap)
            .where(
                or_(
                    AppTagMap.ended_at.is_(None),
                    AppTagMap.ended_at > func.now(),
                )
            )
            .where(AppTagMap.tag_id == self.tag.id)
            .values({AppTagMap.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        db.session.commit()
