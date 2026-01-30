from datetime import datetime, timezone
from typing import Optional, TypedDict, TypeVar
from uuid import uuid4

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.plugins import get_audit_events_hook
from api.plugins.audit_events import AuditEventEnvelope
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook, get_app_group_lifecycle_plugin_to_invoke
from api.services import okta
from api.views.schemas import AuditLogSchema, EventType
from flask import current_app, has_request_context, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectin_polymorphic, with_polymorphic

T = TypeVar("T", bound=OktaGroup)


class GroupDict(TypedDict):
    name: str
    description: str


class CreateGroup:
    def __init__(self, *, group: T | GroupDict, tags: list[str] = [], current_user_id: Optional[str] = None):
        if isinstance(group, dict):
            self.group = OktaGroup(name=group["name"], description=group["description"])
        else:
            self.group = group

        self.tags = Tag.query.filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(tags)).all()

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self, *, _group: Optional[T] = None) -> T:
        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_group = (
            db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(func.lower(OktaGroup.name) == func.lower(self.group.name))
            .filter(OktaGroup.deleted_at.is_(None))
            .first()
        )
        if existing_group is not None:
            return existing_group

        # Make sure the app exists if we're creating an app group
        if (
            type(self.group) is AppGroup
            and App.query.filter(App.id == self.group.app_id).filter(App.deleted_at.is_(None)).first() is None
        ):
            raise ValueError("App for AppGroup does not exist")

        okta_group = okta.create_group(self.group.name, self.group.description)
        if okta_group is None:
            okta_group = okta.list_groups(query_params={"q": self.group.name})[0]
        self.group.id = okta_group.id
        db.session.add(self.group)
        db.session.commit()

        # If this is an app group, add any app tags
        if type(self.group) is AppGroup:
            app_tag_maps = (
                AppTagMap.query.filter(AppTagMap.app_id == self.group.app_id)
                .filter(
                    db.or_(
                        AppTagMap.ended_at.is_(None),
                        AppTagMap.ended_at > db.func.now(),
                    )
                )
                .all()
            )

            for app_tag_map in app_tag_maps:
                db.session.add(
                    OktaGroupTagMap(
                        tag_id=app_tag_map.tag_id,
                        group_id=self.group.id,
                        app_tag_map_id=app_tag_map.id,
                    )
                )
            db.session.commit()

        # Add direct tags
        if len(self.tags) > 0:
            for tag in self.tags:
                db.session.add(
                    OktaGroupTagMap(
                        tag_id=tag.id,
                        group_id=self.group.id,
                    )
                )
            db.session.commit()

        # Invoke app group lifecycle plugin hook, if configured
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)
        if plugin_id is not None:
            try:
                hook = get_app_group_lifecycle_hook()
                hook.group_created(session=db.session, group=self.group, plugin_id=plugin_id)
                db.session.commit()
            except Exception:
                current_app.logger.exception(
                    f"Failed to invoke group_created hook for group {self.group.id} with plugin '{plugin_id}'"
                )
                db.session.rollback()

        # Audit logging
        email = None
        if self.current_user_id is not None:
            email = getattr(db.session.get(OktaUser, self.current_user_id), "email", None)

        group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.group.id)
            .first()
        )

        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_create,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "group": group,
                }
            )
        )

        # Emit audit event to plugins (after DB commit)
        try:
            audit_hook = get_audit_events_hook()
            envelope = AuditEventEnvelope(
                id=uuid4(),
                event_type="group_create",
                timestamp=datetime.now(timezone.utc),
                actor_id=self.current_user_id or "system",
                actor_email=email,
                target_type="group",
                target_id=self.group.id,
                target_name=self.group.name,
                action="created",
                reason="",
                payload={
                    "group_id": self.group.id,
                    "group_name": self.group.name,
                    "group_type": type(self.group).__name__,
                    "is_managed": self.group.is_managed,
                },
                metadata={
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip_address": (
                        request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                        if context
                        else None
                    ),
                },
            )
            audit_hook.audit_event_logged(envelope=envelope)
        except Exception as e:
            current_app.logger.error(f"Failed to emit audit event: {e}", exc_info=True)

        return self.group
