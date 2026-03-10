from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroupTagMap, OktaUser, Tag
from api.operations.modify_groups_time_limit import ModifyGroupsTimeLimit
from api.plugins import get_audit_events_hook
from api.plugins.audit_events import AuditEventEnvelope
from api.views.schemas import AuditLogSchema, EventType
from flask import current_app, has_request_context, request


class ModifyAppTags:
    def __init__(
        self,
        *,
        app: App | str,
        tags_to_add: list[str] = [],
        tags_to_remove: list[str] = [],
        current_user_id: Optional[str],
    ):
        self.app = (
            App.query.filter(App.deleted_at.is_(None))
            .filter(App.id == (app if isinstance(app, str) else app.id))
            .first()
        )

        self.tags_to_add = Tag.query.filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(tags_to_add)).all()

        self.tags_to_remove = Tag.query.filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(tags_to_remove)).all()

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> App:
        if len(self.tags_to_add) > 0:
            # Only add tags that are not already associated with this app
            tag_ids_to_add = [t.id for t in self.tags_to_add]
            existing_tag_maps = (
                AppTagMap.query.filter(
                    db.or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > db.func.now(),
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
                db.session.add(
                    AppTagMap(
                        tag_id=tag_id,
                        app_id=self.app.id,
                    )
                )
            db.session.commit()

            new_app_tag_maps = (
                AppTagMap.query.filter(AppTagMap.tag_id.in_(new_tag_ids_to_add))
                .filter(AppTagMap.app_id == self.app.id)
                .filter(
                    db.or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > db.func.now(),
                    )
                )
                .all()
            )

            all_app_groups = (
                AppGroup.query.filter(AppGroup.app_id == self.app.id).filter(AppGroup.deleted_at.is_(None)).all()
            )
            for app_tag_map in new_app_tag_maps:
                for app_group in all_app_groups:
                    db.session.add(
                        OktaGroupTagMap(
                            tag_id=app_tag_map.tag_id,
                            group_id=app_group.id,
                            app_tag_map_id=app_tag_map.id,
                        )
                    )

            # Handle group time limit constraints when adding tags
            # with time limit contraints to an app
            ModifyGroupsTimeLimit(groups=[g.id for g in all_app_groups], tags=new_tag_ids_to_add).execute()

            db.session.commit()

        if len(self.tags_to_remove) > 0:
            tag_ids_to_remove = [t.id for t in self.tags_to_remove]
            existing_tag_maps_query = (
                AppTagMap.query.filter(AppTagMap.tag_id.in_(tag_ids_to_remove))
                .filter(AppTagMap.app_id == self.app.id)
                .filter(
                    db.or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > db.func.now(),
                    )
                )
            )

            existing_tag_maps = existing_tag_maps_query.all()
            OktaGroupTagMap.query.filter(
                db.or_(
                    OktaGroupTagMap.ended_at.is_(None),
                    OktaGroupTagMap.ended_at > db.func.now(),
                )
            ).filter(
                OktaGroupTagMap.app_tag_map_id.in_([m.id for m in existing_tag_maps]),
            ).update(
                {OktaGroupTagMap.ended_at: db.func.now()},
                synchronize_session="fetch",
            )

            existing_tag_maps_query.update(
                {AppTagMap.ended_at: db.func.now()},
                synchronize_session="fetch",
            )
            db.session.commit()

        # Audit log if tags changed
        if len(self.tags_to_add) > 0 or len(self.tags_to_remove) > 0:
            email = None
            if self.current_user_id is not None:
                email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

            context = has_request_context()
            current_app.logger.info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.app_modify_tags,
                        "user_agent": request.headers.get("User-Agent") if context else None,
                        "ip": (
                            request.headers.get(
                                "X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr)
                            )
                            if context
                            else None
                        ),
                        "current_user_id": self.current_user_id,
                        "current_user_email": email,
                        "app": self.app,
                        "tags_added": self.tags_to_add,
                        "tags_removed": self.tags_to_remove,
                    }
                )
            )

        # Emit audit event to plugins (after DB commit)
        if len(self.tags_to_add) > 0 or len(self.tags_to_remove) > 0:
            try:
                audit_hook = get_audit_events_hook()
                envelope = AuditEventEnvelope(
                    id=uuid4(),
                    event_type="tag_update",
                    timestamp=datetime.now(timezone.utc),
                    actor_id=self.current_user_id or "system",
                    actor_email=email,
                    target_type="app",
                    target_id=self.app.id,
                    target_name=self.app.name,
                    action="tags_modified",
                    reason="",
                    payload={
                        "app_id": self.app.id,
                        "app_name": self.app.name,
                        "tags_added": [t.name for t in self.tags_to_add],
                        "tags_removed": [t.name for t in self.tags_to_remove],
                    },
                    metadata={
                        "user_agent": request.headers.get("User-Agent") if context else None,
                        "ip_address": (
                            request.headers.get(
                                "X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr)
                            )
                            if context
                            else None
                        ),
                    },
                )
                audit_hook.audit_event_logged(envelope=envelope)
            except Exception as e:
                current_app.logger.error(f"Failed to emit audit event: {e}", exc_info=True)

        return self.app
