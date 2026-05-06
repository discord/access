from typing import Optional

import logging

from api.context import get_request_context

from api.extensions import db
from api.models import AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api.schemas import AuditLogSchema, EventType


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
