"""
Modify group tags operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic

from api_v2.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api_v2.operations.modify_groups_time_limit import ModifyGroupsTimeLimit
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary, AuditTagSummary

logger = logging.getLogger(__name__)


class ModifyGroupTags:
    def __init__(
        self,
        db: Session,
        *,
        group: OktaGroup | str,
        tags_to_add: list[str] = [],
        tags_to_remove: list[str] = [],
        current_user_id: Optional[str],
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        self.group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
            .first()
        )

        self.tags_to_add = (
            self.db.query(Tag)
            .filter(Tag.deleted_at.is_(None))
            .filter(Tag.id.in_(tags_to_add))
            .all()
        )

        self.tags_to_remove = (
            self.db.query(Tag)
            .filter(Tag.deleted_at.is_(None))
            .filter(Tag.id.in_(tags_to_remove))
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

    def _log_audit_event(self) -> None:
        """Log the group tag modification audit event."""
        email = None
        if self.current_user_id is not None:
            user = self.db.get(OktaUser, self.current_user_id)
            email = getattr(user, "email", None)

        # Re-fetch the group with proper loading for audit log
        group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.group.id)
            .first()
        )

        # Build group summary
        group_summary = AuditGroupSummary(
            id=group.id,
            name=group.name,
            type=group.__class__.__name__,
        )

        # Add app info if this is an AppGroup
        if isinstance(group, AppGroup) and group.app:
            group_summary.app = AuditAppSummary(
                id=group.app.id,
                name=group.app.name,
            )

        audit_data = {
            "event_type": AuditEventType.GROUP_MODIFY_TAG,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "group": group_summary,
            "tags_added": [
                AuditTagSummary(
                    id=tag.id,
                    name=tag.name,
                    description=tag.description or "",
                    enabled=tag.enabled,
                )
                for tag in self.tags_to_add
            ],
            "tags_removed": [
                AuditTagSummary(
                    id=tag.id,
                    name=tag.name,
                    description=tag.description or "",
                    enabled=tag.enabled,
                )
                for tag in self.tags_to_remove
            ],
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

    def execute(self) -> OktaGroup:
        if len(self.tags_to_add) > 0:
            # Only add tags that are not already associated with the group
            tag_ids_to_add = [t.id for t in self.tags_to_add]
            existing_tag_maps = (
                self.db.query(OktaGroupTagMap)
                .filter(
                    or_(
                        OktaGroupTagMap.ended_at.is_(None),
                        OktaGroupTagMap.ended_at > func.now(),
                    )
                )
                .filter(
                    OktaGroupTagMap.group_id == self.group.id,
                )
                .filter(
                    OktaGroupTagMap.tag_id.in_(tag_ids_to_add),
                )
                .filter(
                    OktaGroupTagMap.app_tag_map_id.is_(None),
                )
                .all()
            )

            new_tag_ids_to_add = set(tag_ids_to_add) - set([m.tag_id for m in existing_tag_maps])

            for tag_id in new_tag_ids_to_add:
                self.db.add(
                    OktaGroupTagMap(
                        tag_id=tag_id,
                        group_id=self.group.id,
                    )
                )

            # Handle group time limit constraints when adding tags
            # with time limit constraints to a group
            ModifyGroupsTimeLimit(
                self.db,
                groups=[self.group.id],
                tags=new_tag_ids_to_add,
            ).execute()

            self.db.commit()

        if len(self.tags_to_remove) > 0:
            self.db.query(OktaGroupTagMap).filter(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            ).filter(
                OktaGroupTagMap.group_id == self.group.id,
            ).filter(
                OktaGroupTagMap.tag_id.in_([t.id for t in self.tags_to_remove]),
            ).filter(
                OktaGroupTagMap.app_tag_map_id.is_(None),
            ).update(
                {OktaGroupTagMap.ended_at: func.now()},
                synchronize_session="fetch",
            )
            self.db.commit()

        # Audit log if tags changed
        if len(self.tags_to_add) > 0 or len(self.tags_to_remove) > 0:
            self._log_audit_event()

        return self.group