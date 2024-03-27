
import random
import string
from typing import Any, Optional, TypedDict

from flask import current_app, has_request_context, request
from sqlalchemy import func

from api.extensions import db
from api.models import OktaUser, Tag
from api.views.schemas import AuditLogSchema, EventType


class TagDict(TypedDict):
    name: str
    description: str
    constraints: dict[str, Any]

class CreateTag:
    def __init__(
        self,
        *,
        tag: Tag | TagDict,
        current_user_id: Optional[str] = None
    ):
        id = self.__generate_id()
        if isinstance(tag, dict):
            self.tag = Tag(id=id, name=tag['name'], description=tag['description'], constraints=tag['constraints'])
        else:
            tag.id = id
            self.tag = tag

        self.current_user_id = (
            getattr(OktaUser.query
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id).first(), 'id', None)
        )

    def execute(self) -> Tag:
        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_tag = (
            Tag.query
            .filter(func.lower(Tag.name) == func.lower(self.tag.name))
            .filter(Tag.deleted_at.is_(None))
            .first()
        )
        if existing_tag is not None:
            return existing_tag

        db.session.add(self.tag)
        db.session.commit()

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), 'email', None)

        context = has_request_context()

        current_app.logger.info(AuditLogSchema().dumps({
            'event_type' : EventType.tag_create,
            'user_agent' : request.headers.get('User-Agent') if context else None,
            'ip' : request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
                        if context else None,
            'current_user_id' : self.current_user_id,
            'current_user_email' : email,
            'tag' : self.tag,
        }))

        return self.tag

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))