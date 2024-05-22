from flask import abort, current_app, g, request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import (
    joinedload,
    selectinload,
    with_polymorphic,
)

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationDecorator
from api.extensions import db
from api.models import AppGroup, AppTagMap, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, Tag
from api.operations import CreateTag, DeleteTag, ModifyGroupsTimeLimit
from api.pagination import paginate
from api.views.schemas import (
    AuditLogSchema,
    DeleteMessageSchema,
    EventType,
    SearchPaginationRequestSchema,
    TagPaginationSchema,
    TagSchema,
)

DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS = (
    "all_group_tags",
    "all_app_tags",
)
ALL_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup, RoleGroup], flat=True)
DEFAULT_LOAD_OPTIONS = (
    selectinload(Tag.active_group_tags).options(
        joinedload(OktaGroupTagMap.active_group.of_type(ALL_GROUP_TYPES)).joinedload(ALL_GROUP_TYPES.AppGroup.app),
        joinedload(OktaGroupTagMap.active_app_tag_mapping).joinedload(AppTagMap.active_app),
    ),
    selectinload(Tag.active_app_tags).joinedload(AppTagMap.active_app),
)


class TagResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(TagSchema)
    def get(self, tag_id: str) -> ResponseReturnValue:
        schema = TagSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)

        tag = (
            Tag.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(db.or_(Tag.id == tag_id, Tag.name == tag_id))
            .order_by(nullsfirst(Tag.deleted_at.desc()))
            .first_or_404()
        )

        return schema.dump(tag)

    @AuthorizationDecorator.require_access_admin
    @FlaskApiSpecDecorators.request_schema(TagSchema)
    @FlaskApiSpecDecorators.response_schema(TagSchema)
    def put(self, tag_id: str) -> ResponseReturnValue:
        tag = (
            Tag.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(Tag.deleted_at.is_(None))
            .filter(db.or_(Tag.id == tag_id, Tag.name == tag_id))
            .first_or_404()
        )
        old_tag = Tag(
            name=tag.name,
            description=tag.description,
            constraints=tag.constraints,
            enabled=tag.enabled,
        )

        schema = TagSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)
        tag_changes = schema.load(request.json)

        if tag_changes.name.lower() != tag.name.lower():
            existing_tag = (
                Tag.query.filter(func.lower(Tag.name) == func.lower(tag_changes.name))
                .filter(Tag.deleted_at.is_(None))
                .first()
            )
            if existing_tag is not None:
                abort(400, "Tag already exists with the same name")

        tag = schema.load(request.json, instance=tag)
        db.session.commit()

        # Handle group time limit constraints when modifying tags
        # with time limit contraints assigned to a group
        tag = (
            Tag.query.options(selectinload(Tag.active_group_tags))
            .filter(Tag.deleted_at.is_(None))
            .filter(Tag.id == tag.id)
            .first()
        )
        ModifyGroupsTimeLimit(
            groups=[tag_map.group_id for tag_map in tag.active_group_tags],
            tags=[tag.id],
        ).execute()

        tag = Tag.query.options(DEFAULT_LOAD_OPTIONS).filter(Tag.deleted_at.is_(None)).filter(Tag.id == tag.id).first()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.tag_modify,
                    "user_agent": request.headers.get("User-Agent"),
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr)),
                    "current_user_id": g.current_user_id,
                    "current_user_email": getattr(db.session.get(OktaUser, g.current_user_id), "email", None),
                    "tag": tag,
                    "old_tag": old_tag,
                }
            )
        )

        return schema.dump(tag)

    @AuthorizationDecorator.require_access_admin
    @FlaskApiSpecDecorators.response_schema(DeleteMessageSchema)
    def delete(self, tag_id: str) -> ResponseReturnValue:
        tag = (
            Tag.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(Tag.deleted_at.is_(None))
            .filter(db.or_(Tag.id == tag_id, Tag.name == tag_id))
            .first_or_404()
        )

        DeleteTag(tag=tag, current_user_id=g.current_user_id).execute()

        return DeleteMessageSchema().dump({"deleted": True})


class TagList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(TagPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchPaginationRequestSchema().load(request.args)

        query = Tag.query.filter(Tag.deleted_at.is_(None)).order_by(func.lower(Tag.name))

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            query = query.filter(
                db.or_(
                    Tag.name.ilike(like_search),
                    Tag.description.ilike(like_search),
                )
            )

        return paginate(
            query,
            TagSchema(
                many=True,
                only=(
                    "id",
                    "name",
                    "description",
                    "constraints",
                    "created_at",
                    "updated_at",
                ),
            ),
        )

    @AuthorizationDecorator.require_access_admin
    @FlaskApiSpecDecorators.request_schema(TagSchema)
    @FlaskApiSpecDecorators.response_schema(TagSchema)
    def post(self) -> ResponseReturnValue:
        schema = TagSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)
        tag = schema.load(request.json)

        existing_tag = (
            Tag.query.filter(func.lower(Tag.name) == func.lower(tag.name)).filter(Tag.deleted_at.is_(None)).first()
        )
        if existing_tag is not None:
            abort(400, "Tag already exists with the same name")

        tag = CreateTag(tag=tag, current_user_id=g.current_user_id).execute()

        tag = Tag.query.options(DEFAULT_LOAD_OPTIONS).filter(Tag.id == tag.id).filter(Tag.deleted_at.is_(None)).first()

        return schema.dump(tag), 201
