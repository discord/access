from flask import abort, current_app, g, request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationDecorator, AuthorizationHelpers
from api.extensions import db
from api.models import App, AppGroup, AppTagMap, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.models.app_group import app_owners_group_description
from api.operations import CreateApp, DeleteApp, ModifyAppTags
from api.pagination import paginate
from api.services import okta
from api.views.schemas import (
    AppPaginationSchema,
    AppSchema,
    AuditLogSchema,
    DeleteMessageSchema,
    EventType,
    SearchPaginationRequestSchema,
)

# Use selectinload for one-to-many eager loading and used joinedload for one-to-one eager loading
DEFAULT_LOAD_OPTIONS = (
    selectinload(App.active_owner_app_groups).options(
        selectinload(AppGroup.active_user_memberships).options(
            joinedload(OktaUserGroupMember.active_user),
            joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
        ),
        selectinload(AppGroup.active_user_ownerships).options(
            joinedload(OktaUserGroupMember.active_user),
            joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
        ),
    ),
    selectinload(App.active_non_owner_app_groups).options(
        selectinload(AppGroup.active_user_memberships).options(
            joinedload(OktaUserGroupMember.active_user),
            joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
        ),
        selectinload(AppGroup.active_user_ownerships).options(
            joinedload(OktaUserGroupMember.active_user),
            joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
        ),
    ),
    selectinload(App.active_app_tags).joinedload(AppTagMap.active_tag),
)

DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS = (
    "app_groups",
    "active_app_groups",
    "active_owner_app_groups.all_user_memberships_and_ownerships",
    "active_owner_app_groups.active_user_memberships_and_ownerships",
    "active_owner_app_groups.active_role_member_mappings",
    "active_owner_app_groups.active_role_owner_mappings",
    "active_owner_app_groups.active_non_role_user_memberships",
    "active_owner_app_groups.active_non_role_user_ownerships",
    "active_owner_app_groups.all_role_mappings",
    "active_owner_app_groups.active_role_mappings",
    "active_owner_app_groups.active_group_tags",
    "active_owner_app_groups.all_group_tags",
    "active_non_owner_app_groups.all_user_memberships_and_ownerships",
    "active_non_owner_app_groups.active_user_memberships_and_ownerships",
    "active_non_owner_app_groups.active_role_member_mappings",
    "active_non_owner_app_groups.active_role_owner_mappings",
    "active_non_owner_app_groups.active_non_role_user_memberships",
    "active_non_owner_app_groups.active_non_role_user_ownerships",
    "active_non_owner_app_groups.all_role_mappings",
    "active_non_owner_app_groups.active_role_mappings",
    "active_non_owner_app_groups.all_group_tags",
    "active_non_owner_app_groups.active_group_tags",
    "all_app_tags",
)


class AppResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(AppSchema)
    def get(self, app_id: str) -> ResponseReturnValue:
        schema = AppSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)
        app = (
            App.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(App.deleted_at.is_(None))
            .filter(db.or_(App.id == app_id, App.name == app_id))
            .first_or_404()
        )
        return schema.dump(app)

    @AuthorizationDecorator.require_app_owner_or_access_admin_for_app
    @FlaskApiSpecDecorators.request_schema(AppSchema)
    @FlaskApiSpecDecorators.response_schema(AppSchema)
    def put(self, app: App) -> ResponseReturnValue:
        schema = AppSchema(
            exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS,
        )
        app_changes = schema.load(request.json)

        if app_changes.name.lower() != app.name.lower():
            existing_app = (
                App.query.filter(func.lower(App.name) == func.lower(app_changes.name))
                .filter(App.deleted_at.is_(None))
                .first()
            )
            if existing_app is not None:
                abort(400, "App already exists with the same name")

        json_data = request.get_json()
        if "tags_to_remove" in json_data:
            if len(json_data["tags_to_remove"]) > 0 and not AuthorizationHelpers.is_access_admin():
                abort(
                    403,
                    "Current user is not an Access Admin and not allowed to remove tags from this app",
                )

        # Prevent non-tag access app modification
        if app.name == App.ACCESS_APP_RESERVED_NAME:
            if len(json_data.get("tags_to_add", [])) > 0 or len(json_data.get("tags_to_remove", [])) > 0:
                ModifyAppTags(
                    app=app,
                    tags_to_add=json_data.get("tags_to_add", []),
                    tags_to_remove=json_data.get("tags_to_remove", []),
                    current_user_id=g.current_user_id,
                ).execute()
                app = (
                    App.query.options(DEFAULT_LOAD_OPTIONS)
                    .filter(App.deleted_at.is_(None))
                    .filter(App.id == app.id)
                    .first()
                )
                return schema.dump(app)
            else:
                abort(400, "Only tags can be modified for the Access application")

        old_app_name = app.name
        app = schema.load(request.json, instance=app)

        # Update all app group names when updating app name
        if app.name != old_app_name:
            old_name_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{old_app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
            new_name_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
            app_groups = AppGroup.query.filter(AppGroup.app_id == app.id)
            for app_group in app_groups:
                if app_group.name.startswith(old_name_prefix):
                    group_name_suffix = app_group.name[len(old_name_prefix) :]
                    app_group.name = "{}{}".format(
                        new_name_prefix,
                        group_name_suffix,
                    )
                else:
                    app_group.name = "{}{}".format(
                        new_name_prefix,
                        app_group.name,
                    )
                if app_group.is_owner:
                    app_group.description = app_owners_group_description(app.name)
                if app_group.deleted_at is None:
                    okta.update_group(app_group.id, app_group.name, app_group.description)

        db.session.commit()

        # Update tags associated with this app
        ModifyAppTags(
            app=app,
            tags_to_add=json_data.get("tags_to_add", []),
            tags_to_remove=json_data.get("tags_to_remove", []),
            current_user_id=g.current_user_id,
        ).execute()

        app = App.query.options(DEFAULT_LOAD_OPTIONS).filter(App.deleted_at.is_(None)).filter(App.id == app.id).first()

        # Audit logging gnly log if app name changed
        if old_app_name.lower() != app.name.lower():
            current_app.logger.info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.app_modify_name,
                        "user_agent": request.headers.get("User-Agent"),
                        "ip": request.headers.get(
                            "X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr)
                        ),
                        "current_user_id": g.current_user_id,
                        "current_user_email": getattr(db.session.get(OktaUser, g.current_user_id), "email", None),
                        "app": app,
                        "old_app_name": old_app_name,
                    }
                )
            )

        return schema.dump(app)

    @AuthorizationDecorator.require_app_owner_or_access_admin_for_app
    @FlaskApiSpecDecorators.response_schema(DeleteMessageSchema)
    def delete(self, app: App) -> ResponseReturnValue:
        # Prevent access app deletion
        if app.name == App.ACCESS_APP_RESERVED_NAME:
            abort(400, "The Access Application cannot be deleted")

        DeleteApp(app=app, current_user_id=g.current_user_id).execute()

        return DeleteMessageSchema().dump({"deleted": True})


class AppList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(AppPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchPaginationRequestSchema().load(request.args)

        query = App.query.filter(App.deleted_at.is_(None)).order_by(func.lower(App.name))

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            query = query.filter(
                db.or_(
                    App.name.ilike(like_search),
                    App.description.ilike(like_search),
                )
            )

        return paginate(
            query,
            AppSchema(
                many=True,
                only=(
                    "id",
                    "created_at",
                    "updated_at",
                    "deleted_at",
                    "name",
                    "description",
                ),
            ),
        )

    @AuthorizationDecorator.require_access_admin_or_app_creator
    @FlaskApiSpecDecorators.request_schema(AppSchema)
    @FlaskApiSpecDecorators.response_schema(AppSchema)
    def post(self) -> ResponseReturnValue:
        schema = AppSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)
        app = schema.load(request.json)

        existing_app = (
            App.query.filter(func.lower(App.name) == func.lower(app.name)).filter(App.deleted_at.is_(None)).first()
        )
        if existing_app is not None:
            abort(400, "App already exists with the same name")

        owner_id = None
        # Is the current user a real user in the database?
        if db.session.get(OktaUser, g.current_user_id) is not None:
            owner_id = g.current_user_id
        # Allow using the initial_owner_id from the request body
        json_data = request.get_json()
        if "initial_owner_id" in json_data:
            owner = (
                OktaUser.query.filter(OktaUser.deleted_at.is_(None))
                .filter(
                    db.or_(
                        OktaUser.id == json_data["initial_owner_id"],
                        OktaUser.email.ilike(json_data["initial_owner_id"]),
                    )
                )
                .first()
            )
            if owner is None:
                abort(400, "Given App initial_owner_id is not a valid user")

            owner_id = owner.id

        if owner_id is None:
            abort(400, "App initial_owner_id is required")

        owner_role_ids = []
        if "initial_owner_role_ids" in json_data:
            owner_roles = (
                RoleGroup.query.filter(RoleGroup.id.in_(json_data["initial_owner_role_ids"]))
                .filter(RoleGroup.deleted_at.is_(None))
                .all()
            )
            owner_role_ids = [role.id for role in owner_roles]

            if len(owner_role_ids) != len(json_data["initial_owner_role_ids"]):
                abort(400, "Given App initial_owner_role_ids contains invalid role ids")

        initial_additional_app_groups = []
        app_group_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        owner_group_name = f"{app_group_prefix}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        if "initial_additional_app_groups" in json_data:
            for initial_app_group in json_data["initial_additional_app_groups"]:
                name = initial_app_group["name"]
                if not name.startswith(app_group_prefix):
                    abort(400, f"Additional app group name must be prefixed with {app_group_prefix}")
                if name == owner_group_name:
                    abort(
                        400, f"Cannot specify {AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX} group as an additional app group"
                    )

                initial_additional_app_groups.append(initial_app_group)

        app = CreateApp(
            owner_id=owner_id,
            owner_role_ids=owner_role_ids,
            app=app,
            tags=json_data.get("tags_to_add", []),
            additional_app_groups=initial_additional_app_groups,
            current_user_id=g.current_user_id,
        ).execute()

        a = App.query.options(DEFAULT_LOAD_OPTIONS).filter(App.deleted_at.is_(None)).filter(App.id == app.id).first()
        return schema.dump(a), 201
