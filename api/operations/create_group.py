from typing import Optional, TypedDict, TypeVar

from flask import current_app, has_request_context, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectin_polymorphic, with_polymorphic

from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.plugins.metrics_reporter import get_metrics_reporter_hook
from api.services import okta
from api.views.schemas import AuditLogSchema, EventType

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

        self.metrics_hook = get_metrics_reporter_hook()

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

        # Record metrics for role creation
        if isinstance(self.group, RoleGroup):
            self.metrics_hook.record_counter(
                "role.created",
                tags={
                    "created_by_user_type": "admin" if self.current_user_id else "system",
                }
            )

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
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.current_user_id,
                    "current_user_email": email,
                    "group": group,
                }
            )
        )

        return self.group
