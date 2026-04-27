"""Apps router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import (
    require_access_admin_or_app_creator,
    require_app_owner_or_access_admin_for_app,
)
from api.config import settings
from api.database import DbSession
from api.extensions import db as _db
from api.models import (
    App,
    AppGroup,
    AppTagMap,
    OktaGroupTagMap,
    OktaUserGroupMember,
    RoleGroupMap,
)
from api.pagination import paginate
from api.schemas import AppOut, AppSummary, DeleteMessage
from api.schemas._serialize import safe_dump


# Eager-load options for an `App` response so the `active_owner_app_groups`
# and `active_non_owner_app_groups` lists carry enough nested data for the
# frontend's apps page to render without lazy-load errors.
_APP_GROUP_LOAD = (
    selectinload(AppGroup.active_user_memberships).options(
        joinedload(OktaUserGroupMember.active_user),
        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
            RoleGroupMap.active_role_group
        ),
    ),
    selectinload(AppGroup.active_user_ownerships).options(
        joinedload(OktaUserGroupMember.active_user),
        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
            RoleGroupMap.active_role_group
        ),
    ),
    selectinload(AppGroup.active_group_tags).options(
        joinedload(OktaGroupTagMap.active_tag),
        joinedload(OktaGroupTagMap.active_app_tag_mapping),
    ),
)
APP_LOAD_OPTIONS = (
    selectinload(App.active_app_tags).joinedload(AppTagMap.active_tag),
    selectinload(App.active_owner_app_groups).options(*_APP_GROUP_LOAD),
    selectinload(App.active_non_owner_app_groups).options(*_APP_GROUP_LOAD),
)


def _validate_description(value: Any, field_provided: bool) -> str:
    """Mirror the Marshmallow `context_aware_description_field` semantics."""
    if not field_provided:
        if settings.REQUIRE_DESCRIPTIONS:
            raise HTTPException(400, "Description is required.")
        return ""
    if value == "" and settings.REQUIRE_DESCRIPTIONS:
        raise HTTPException(400, "Description must be between 1 and 1024 characters")
    if value is None or value == "":
        if settings.REQUIRE_DESCRIPTIONS:
            raise HTTPException(400, "Description is required.")
        return ""
    if not isinstance(value, str):
        raise HTTPException(400, "Description must be a string")
    if len(value) > 1024:
        raise HTTPException(400, "Description must be 1024 characters or less")
    return value

router = APIRouter(prefix="/api/apps", tags=["apps"])
_adapter = TypeAdapter(AppOut)
_summary_adapter = TypeAdapter(AppSummary)


@router.get("", name="apps")
def list_apps(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    query = db.query(App).filter(App.deleted_at.is_(None)).order_by(func.lower(App.name))
    if q:
        like = f"%{q}%"
        query = query.filter(_db.or_(App.name.ilike(like), App.description.ilike(like)))
    return paginate(request, query, _summary_adapter)


@router.get("/{app_id}", name="app_by_id")
def get_app(app_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    app = (
        db.query(App)
        .options(*APP_LOAD_OPTIONS)
        .filter(_db.or_(App.id == app_id, App.name == app_id))
        .first()
    )
    if app is None:
        raise HTTPException(404, "Not Found")
    return safe_dump(_adapter, app)


@router.post("", name="apps_create", status_code=201)
def post_app(
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: str = Depends(require_access_admin_or_app_creator),
) -> dict[str, Any]:
    from api.models import AppGroup as _AppGroup, OktaUser, RoleGroup
    from api.operations import CreateApp

    body = body or {}
    if not body.get("name"):
        raise HTTPException(400, "App name is required")
    description = _validate_description(body.get("description"), "description" in body)
    body["description"] = description

    # Reject duplicates by name.
    existing = (
        db.query(App)
        .filter(func.lower(App.name) == func.lower(body["name"]))
        .filter(App.deleted_at.is_(None))
        .first()
    )
    if existing is not None:
        raise HTTPException(400, "App already exists with the same name")

    # Default owner = current user; explicit initial_owner_id wins.
    owner_id: str | None = None
    if db.get(OktaUser, current_user_id) is not None:
        owner_id = current_user_id
    if "initial_owner_id" in body:
        owner = (
            db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(
                _db.or_(
                    OktaUser.id == body["initial_owner_id"],
                    OktaUser.email.ilike(body["initial_owner_id"]),
                )
            )
            .first()
        )
        if owner is None:
            raise HTTPException(400, "Given App initial_owner_id is not a valid user")
        owner_id = owner.id

    if owner_id is None:
        raise HTTPException(400, "App initial_owner_id is required")

    owner_role_ids: list[str] = []
    if "initial_owner_role_ids" in body:
        roles = (
            db.query(RoleGroup)
            .filter(RoleGroup.id.in_(body["initial_owner_role_ids"]))
            .filter(RoleGroup.deleted_at.is_(None))
            .all()
        )
        owner_role_ids = [r.id for r in roles]
        if len(owner_role_ids) != len(body["initial_owner_role_ids"]):
            raise HTTPException(400, "Given App initial_owner_role_ids contains invalid role ids")

    name = body["name"]
    app_group_prefix = f"{_AppGroup.APP_GROUP_NAME_PREFIX}{name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    owner_group_name = f"{app_group_prefix}{_AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"

    initial_additional_app_groups: list[dict[str, Any]] = []
    if "initial_additional_app_groups" in body:
        for ig in body["initial_additional_app_groups"]:
            n = ig.get("name", "")
            if not n.startswith(app_group_prefix):
                raise HTTPException(400, f"Additional app group name must be prefixed with {app_group_prefix}")
            if n == owner_group_name:
                raise HTTPException(
                    400,
                    f"Cannot specify {_AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX} group as an additional app group",
                )
            initial_additional_app_groups.append(ig)

    app_obj = App(name=name, description=body.get("description", ""))
    created = CreateApp(
        app=app_obj,
        owner_id=owner_id,
        owner_role_ids=owner_role_ids,
        additional_app_groups=initial_additional_app_groups,
        tags=body.get("tags_to_add", []),
        current_user_id=current_user_id,
    ).execute()
    refreshed = (
        db.query(App)
        .options(*APP_LOAD_OPTIONS)
        .filter(App.id == created.id)
        .first()
    )
    return safe_dump(_adapter, refreshed)


@router.put("/{app_id}", name="app_by_id_put")
def put_app(
    app_id: str,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    app_obj=Depends(require_app_owner_or_access_admin_for_app),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    import copy

    from api.auth.permissions import is_access_admin
    from api.models import AppGroup as _AppGroup
    from api.models.app_group import app_owners_group_description
    from api.operations import ModifyAppTags, ModifyGroupDetails
    from api.plugins.app_group_lifecycle import merge_app_lifecycle_plugin_data

    from api.plugins.app_group_lifecycle import validate_app_group_lifecycle_plugin_app_config

    body = body or {}
    if "description" in body:
        body["description"] = _validate_description(body["description"], True)

    # Snapshot the old plugin state before any mutation.
    old_app_group_lifecycle_plugin = app_obj.app_group_lifecycle_plugin
    old_plugin_data_for_audit_pre = copy.deepcopy(app_obj.plugin_data) if app_obj.plugin_data else {}

    # Validate plugin_data against the configured plugin's schema before mutating.
    new_plugin_id = body.get("app_group_lifecycle_plugin", app_obj.app_group_lifecycle_plugin)
    if "plugin_data" in body and new_plugin_id is not None:
        try:
            errors = validate_app_group_lifecycle_plugin_app_config(body["plugin_data"], new_plugin_id)
        except ValueError as e:
            raise HTTPException(400, f"plugin_data: {e}") from e
        if errors:
            raise HTTPException(400, f"plugin_data: {errors}")

    # Plugin config changes require access admin.
    new_plugin = body.get("app_group_lifecycle_plugin", app_obj.app_group_lifecycle_plugin)
    new_plugin_data = body.get("plugin_data") if "plugin_data" in body else None
    if (
        new_plugin != app_obj.app_group_lifecycle_plugin
        or (new_plugin_data is not None and new_plugin_data != (app_obj.plugin_data or {}))
    ) and not is_access_admin(db, current_user_id):
        raise HTTPException(
            403, "Only Access owners are allowed to configure plugins at the app level"
        )

    # Reject duplicate name on rename
    new_name: str | None = body.get("name")
    if new_name and new_name.lower() != app_obj.name.lower():
        existing = (
            db.query(App)
            .filter(func.lower(App.name) == func.lower(new_name))
            .filter(App.deleted_at.is_(None))
            .first()
        )
        if existing is not None:
            raise HTTPException(400, "App already exists with the same name")

    # tags_to_remove gated on admin
    if "tags_to_remove" in body and len(body["tags_to_remove"]) > 0:
        if not is_access_admin(db, current_user_id):
            raise HTTPException(
                403, "Current user is not an Access Admin and not allowed to remove tags from this app"
            )

    # Built-in Access app: only tags can be modified
    if app_obj.name == App.ACCESS_APP_RESERVED_NAME:
        if len(body.get("tags_to_add", [])) > 0 or len(body.get("tags_to_remove", [])) > 0:
            ModifyAppTags(
                app=app_obj,
                tags_to_add=body.get("tags_to_add", []),
                tags_to_remove=body.get("tags_to_remove", []),
                current_user_id=current_user_id,
            ).execute()
            refreshed = (
                db.query(App)
                .options(*APP_LOAD_OPTIONS)
                .filter(App.id == app_obj.id)
                .first()
            )
            return safe_dump(_adapter, refreshed)
        raise HTTPException(400, "Only tags can be modified for the Access application")

    old_app_name = app_obj.name
    old_plugin_data_for_audit = copy.deepcopy(app_obj.plugin_data) if app_obj.plugin_data else {}
    old_plugin_data = app_obj.plugin_data

    if "name" in body:
        app_obj.name = body["name"]
    if "description" in body:
        app_obj.description = body["description"]
    if "app_group_lifecycle_plugin" in body:
        app_obj.app_group_lifecycle_plugin = body["app_group_lifecycle_plugin"]
    if new_plugin_data is not None:
        app_obj.plugin_data = new_plugin_data
        if old_plugin_data and app_obj.plugin_data != old_plugin_data:
            for key in old_plugin_data:
                if key not in app_obj.plugin_data:
                    app_obj.plugin_data[key] = old_plugin_data[key]
            merge_app_lifecycle_plugin_data(app_obj, old_plugin_data)

    # Rename associated app groups when the app name changed
    if app_obj.name != old_app_name:
        old_prefix = (
            f"{_AppGroup.APP_GROUP_NAME_PREFIX}{old_app_name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        )
        new_prefix = (
            f"{_AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        )
        app_groups = db.query(_AppGroup).filter(_AppGroup.app_id == app_obj.id).all()
        for ag in app_groups:
            if ag.name.startswith(old_prefix):
                suffix = ag.name[len(old_prefix):]
                new_group_name = f"{new_prefix}{suffix}"
            else:
                new_group_name = f"{new_prefix}{ag.name}"
            new_description = app_owners_group_description(app_obj.name) if ag.is_owner else None
            ModifyGroupDetails(group=ag, name=new_group_name, description=new_description).execute()

    db.commit()

    ModifyAppTags(
        app=app_obj,
        tags_to_add=body.get("tags_to_add", []),
        tags_to_remove=body.get("tags_to_remove", []),
        current_user_id=current_user_id,
    ).execute()

    refreshed = (
        db.query(App)
        .options(*APP_LOAD_OPTIONS)
        .filter(App.id == app_obj.id)
        .first()
    )

    # Audit logging — plugin assignment / configuration changes
    if (
        old_app_group_lifecycle_plugin != getattr(refreshed, "app_group_lifecycle_plugin", None)
        or old_plugin_data_for_audit_pre != (refreshed.plugin_data or {})
    ):
        from api.context import get_request_context
        from api.models import OktaUser
        from api.schemas import AuditLogSchema, EventType
        import logging as _logging

        _ctx = get_request_context()
        email = (
            getattr(db.get(OktaUser, current_user_id), "email", None)
            if current_user_id is not None
            else None
        )
        _logging.getLogger("api.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_modify_plugin,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": current_user_id,
                    "current_user_email": email,
                    "app": refreshed,
                    "old_app_group_lifecycle_plugin": old_app_group_lifecycle_plugin,
                    "old_plugin_data": old_plugin_data_for_audit_pre,
                }
            )
        )
    return safe_dump(_adapter, refreshed)


@router.delete("/{app_id}", name="app_by_id_delete")
def delete_app(
    app_id: str,
    db: DbSession,
    app_obj=Depends(require_app_owner_or_access_admin_for_app),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from api.operations import DeleteApp

    DeleteApp(app=app_obj, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True).model_dump()
