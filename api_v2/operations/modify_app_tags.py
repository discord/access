"""
Modify app tags operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api_v2.models import App, AppGroup, AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api_v2.operations.modify_groups_time_limit import ModifyGroupsTimeLimit
from api_v2.schemas import AuditEventType, AuditLogRead, AuditAppSummary, AuditTagSummary

logger = logging.getLogger(__name__)


class ModifyAppTags:
    def __init__(
        self,
        db: Session,
        *,
        app: App | str,
        tags_to_add: list[str] = [],
        tags_to_remove: list[str] = [],
        current_user_id: Optional[str],
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        self.app = (
            self.db.query(App)
            .filter(App.deleted_at.is_(None))
            .filter(App.id == (app if isinstance(app, str) else app.id))
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
        """Log the app tag modification audit event."""
        email = None
        if self.current_user_id is not None:
            user = self.db.get(OktaUser, self.current_user_id)
            email = getattr(user, "email", None)

        audit_data = {
            "event_type": AuditEventType.APP_MODIFY_TAG,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.current_user_id,
            "current_user_email": email,
            "app": AuditAppSummary(
                id=self.app.id,
                name=self.app.name,
            ),
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

    def execute(self) -> App:
        if len(self.tags_to_add) > 0:
            # Only add tags that are not already associated with this app
            tag_ids_to_add = [t.id for t in self.tags_to_add]
            existing_tag_maps = (
                self.db.query(AppTagMap)
                .filter(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
                .filter(
                    AppTagMap.app_id == self.app.id,
                )
                .filter(
                    AppTagMap.tag_id.in_(tag_ids_to_add),
                )
                .all()
            )

            new_tag_ids_to_add = set(tag_ids_to_add) - set([m.tag_id for m in existing_tag_maps])

            for tag_id in new_tag_ids_to_add:
                self.db.add(
                    AppTagMap(
                        tag_id=tag_id,
                        app_id=self.app.id,
                    )
                )
            self.db.commit()

            new_app_tag_maps = (
                self.db.query(AppTagMap)
                .filter(AppTagMap.tag_id.in_(new_tag_ids_to_add))
                .filter(AppTagMap.app_id == self.app.id)
                .filter(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
                .all()
            )

            all_app_groups = (
                self.db.query(AppGroup)
                .filter(AppGroup.app_id == self.app.id)
                .filter(AppGroup.deleted_at.is_(None))
                .all()
            )
            for app_tag_map in new_app_tag_maps:
                for app_group in all_app_groups:
                    self.db.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=app_group.id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )

            # Handle group time limit constraints when adding tags
            # with time limit constraints to an app
            ModifyGroupsTimeLimit(
                self.db,
                groups=[g.id for g in all_app_groups],
                tags=new_tag_ids_to_add,
            ).execute()

            self.db.commit()

        if len(self.tags_to_remove) > 0:
            tag_ids_to_remove = [t.id for t in self.tags_to_remove]
            existing_tag_maps_query = (
                self.db.query(AppTagMap)
                .filter(AppTagMap.tag_id.in_(tag_ids_to_remove))
                .filter(AppTagMap.app_id == self.app.id)
                .filter(
                    or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > func.now(),
                    )
                )
            )

            existing_tag_maps = existing_tag_maps_query.all()
            self.db.query(OktaGroupTagMap).filter(
                or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > func.now(),
                )
            ).filter(
                OktaGroupTagMap.app_tag_map_id.in_([m.id for m in existing_tag_maps]),
            ).update(
                {OktaGroupTagMap.ended_at: func.now()},
                synchronize_session="fetch",
            )

            existing_tag_maps_query.update(
                {AppTagMap.ended_at: func.now()},
                synchronize_session="fetch",
            )
            self.db.commit()

        # Audit log if tags changed
        if len(self.tags_to_add) > 0 or len(self.tags_to_remove) > 0:
            self._log_audit_event()

        return self.app