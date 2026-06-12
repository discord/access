import random
import string
from typing import Any, Optional, TypedDict

import logging

from api.context import get_request_context
from sqlalchemy import func, select

from api.extensions import db
from api.models import OktaUser, Tag
from api.schemas import AuditLogSchema, EventType


class TagDict(TypedDict):
    name: str
    description: str
    constraints: dict[str, Any]


class CreateTag:
    def __init__(self, *, tag: Tag | TagDict, current_user_id: Optional[str] = None):
        id = self.__generate_id()
        if isinstance(tag, dict):
            self.tag = Tag(id=id, name=tag["name"], description=tag["description"], constraints=tag["constraints"])
        else:
            tag.id = id
            self.tag = tag

        self._current_user_id_arg = current_user_id

    async def _resolve(self) -> None:
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

    async def execute(self) -> Tag:
        await self._resolve()
        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_tag = (
            await db.session.scalars(
                select(Tag).where(func.lower(Tag.name) == func.lower(self.tag.name)).where(Tag.deleted_at.is_(None))
            )
        ).first()
        if existing_tag is not None:
            return existing_tag

        db.session.add(self.tag)
        await db.session.commit()

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(await db.session.get(OktaUser, self.current_user_id), "email", None)

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.tag_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "tag": self.tag,
                }
            )
        )

        return self.tag

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
