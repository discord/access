"""
Create group operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from typing import Optional, TypedDict, TypeVar

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic, with_polymorphic

from api_v2.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary
from api_v2.services import okta

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=OktaGroup)


class GroupDict(TypedDict):
    name: str
    description: str


class CreateGroup:
    def __init__(
        self,
        db: Session,
        *,
        group: T | GroupDict,
        tags: list[str] = [],
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request
        
        if isinstance(group, dict):
            self.group = OktaGroup(name=group["name"], description=group["description"])
        else:
            self.group = group

        self.tags = (
            self.db.query(Tag)
            .filter(Tag.deleted_at.is_(None))
            .filter(Tag.id.in_(tags))
            .all()
        )

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def _log_audit_event(self, group_id: str) -> None:
        """Log the group creation event using structured audit logging."""
        # Get user email if current_user_id is set
        email = None
        if self.current_user_id is not None:
            user = self.db.get(OktaUser, self.current_user_id)
            email = getattr(user, "email", None)

        # Get the created group with relationships for audit logging
        group = (
            self.db.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
            )
            .filter(OktaGroup.id == group_id)
            .first()
        )

        # Build audit log data
        audit_data = {
            "event_type": AuditEventType.GROUP_CREATE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
        }

        # Extract request information if available
        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            # Get IP from headers, preferring X-Forwarded-For, then X-Real-IP, then client host
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For") or
                self.request.headers.get("X-Real-IP") or
                self.request.client.host if self.request.client else None
            )

        # Add group information
        if group:
            group_summary = AuditGroupSummary(
                id=group.id,
                name=group.name,
                type=group.type,
                app=AuditAppSummary(
                    id=group.app.id,
                    name=group.app.name
                ) if hasattr(group, 'app') and group.app else None
            )
            audit_data["group"] = group_summary

        # Create structured audit log
        audit_log = AuditLogRead(**audit_data)
        
        # Output structured log as JSON
        logger.info(audit_log.model_dump_json(exclude_none=True))

    def execute(self, *, _group: Optional[T] = None) -> T:
        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_group = (
            self.db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(func.lower(OktaGroup.name) == func.lower(self.group.name))
            .filter(OktaGroup.deleted_at.is_(None))
            .first()
        )
        if existing_group is not None:
            return existing_group

        # Make sure the app exists if we're creating an app group
        if type(self.group) is AppGroup:
            app_exists = (
                self.db.query(App)
                .filter(App.id == self.group.app_id)
                .filter(App.deleted_at.is_(None))
                .first()
                is not None
            )
            if not app_exists:
                raise ValueError("App for AppGroup does not exist")

        # Create the group in Okta
        okta_group = okta.create_group(self.group.name, self.group.description)
        if okta_group is None:
            # If group creation returns None, try to find it
            okta_group = okta.list_groups(query_params={"q": self.group.name})[0]
        self.group.id = okta_group.id
        
        self.db.add(self.group)
        self.db.commit()

        # If this is an app group, add any app tags
        if type(self.group) is AppGroup:
            app_tag_maps = (
                self.db.query(AppTagMap)
                .filter(AppTagMap.app_id == self.group.app_id)
                .filter(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
                .all()
            )

            for app_tag_map in app_tag_maps:
                self.db.add(
                    OktaGroupTagMap(
                        tag_id=app_tag_map.tag_id,
                        group_id=self.group.id,
                        app_tag_map_id=app_tag_map.id,
                    )
                )
            self.db.commit()

        # Add direct tags
        if len(self.tags) > 0:
            for tag in self.tags:
                self.db.add(
                    OktaGroupTagMap(
                        tag_id=tag.id,
                        group_id=self.group.id,
                    )
                )
            self.db.commit()

        # Log audit event
        self._log_audit_event(self.group.id)
        
        # Return the created group
        return self.db.get(type(self.group), self.group.id)