"""Groups router. Endpoints:

GET    /api/groups
POST   /api/groups
GET    /api/groups/{group_id}
PUT    /api/groups/{group_id}
DELETE /api/groups/{group_id}
GET    /api/groups/{group_id}/members
PUT    /api/groups/{group_id}/members
GET    /api/groups/{group_id}/audit         redirects to /api/audit/users
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic
from starlette.requests import Request

from api.auth import permissions as _perms
from api.auth.dependencies import CurrentUserId
from api.auth.permissions import (
    is_access_admin,
    is_app_owner_group_owner,
)
from api.database import DbSession
from api.extensions import db as _db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import (
    CreateGroup,
    DeleteGroup,
    ModifyGroupDetails,
    ModifyGroupTags,
    ModifyGroupType,
    ModifyGroupUsers,
)
from api.operations.constraints import CheckForReason, CheckForSelfAdd
from api.pagination import paginate
from api.plugins.app_group_lifecycle import merge_app_lifecycle_plugin_data
from api.schemas import DeleteMessage, GroupDetail, GroupSummary
from api.schemas._serialize import safe_dump
from api.schemas.requests_schemas import GroupMember

import copy

router = APIRouter(prefix="/api/groups", tags=["groups"])

ROLE_ASSOCIATED_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup])
DEFAULT_LOAD_OPTIONS = (
    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
    selectinload(OktaGroup.active_user_memberships).options(
        joinedload(OktaUserGroupMember.active_user),
        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
    ),
    selectinload(OktaGroup.active_user_ownerships).options(
        joinedload(OktaUserGroupMember.active_user),
        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
    ),
    selectinload(OktaGroup.active_role_member_mappings).joinedload(RoleGroupMap.active_role_group),
    selectinload(OktaGroup.active_role_owner_mappings).joinedload(RoleGroupMap.active_role_group),
    joinedload(AppGroup.app),
    selectinload(OktaGroup.active_group_tags).options(
        joinedload(OktaGroupTagMap.active_app_tag_mapping),
        joinedload(OktaGroupTagMap.active_tag),
    ),
)

_group_adapter = TypeAdapter(GroupDetail)
_group_summary_adapter = TypeAdapter(GroupSummary)


def _load_group_with_options(db: DbSession, group_id: str) -> OktaGroup | None:
    return (
        db.query(OktaGroup)
        .options(*DEFAULT_LOAD_OPTIONS)
        .filter(_db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
        .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
        .first()
    )


@router.get("", name="groups")
def list_groups(request: Request, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    q = request.query_params.get("q", "")
    managed = request.query_params.get("managed")

    query = (
        db.query(OktaGroup)
        .options(
            selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
            selectinload(OktaGroup.active_group_tags).options(
                joinedload(OktaGroupTagMap.active_app_tag_mapping),
                joinedload(OktaGroupTagMap.active_tag),
            ),
            joinedload(AppGroup.app),
        )
        .filter(OktaGroup.deleted_at.is_(None))
        .order_by(func.lower(OktaGroup.name))
    )
    if q:
        like = f"%{q}%"
        query = query.filter(_db.or_(OktaGroup.name.ilike(like), OktaGroup.description.ilike(like)))
    if managed is not None and managed != "":
        query = query.filter(OktaGroup.is_managed == (managed.lower() == "true"))

    return paginate(request, query, _group_summary_adapter)


@router.get("/{group_id}", name="group_by_id")
def get_group(group_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    group = _load_group_with_options(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return safe_dump(_group_adapter, group)


def _validate_description(value: Any, field_provided: bool) -> str:
    """Validate `description` against `settings.REQUIRE_DESCRIPTIONS`."""
    from api.config import settings

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


@router.post("", name="groups_create", status_code=201)
def post_group(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Create a new group. Body is the polymorphic group input + tags_to_add."""
    body = body or {}
    description = _validate_description(body.get("description"), "description" in body)
    body["description"] = description
    group_type = body.get("type")
    if group_type not in ("okta_group", "role_group", "app_group"):
        raise HTTPException(400, "Invalid or missing group type")

    if group_type == "okta_group":
        group: OktaGroup = OktaGroup(
            name=body.get("name", ""),
            description=body.get("description", ""),
        )
    elif group_type == "role_group":
        group = RoleGroup(
            name=body.get("name", ""),
            description=body.get("description", ""),
        )
    else:
        group = AppGroup(
            name=body.get("name", ""),
            description=body.get("description", ""),
            app_id=body.get("app_id"),
            is_owner=body.get("is_owner", False),
            plugin_data=body.get("plugin_data") or {},
        )

    if not (
        is_access_admin(db, current_user_id)
        or is_app_owner_group_owner(db, current_user_id, app_group=group if isinstance(group, AppGroup) else None)
    ):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    existing = (
        db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
        .filter(func.lower(OktaGroup.name) == func.lower(group.name))
        .filter(OktaGroup.deleted_at.is_(None))
        .first()
    )
    if existing is not None:
        raise HTTPException(400, "Group already exists with the same name")

    if type(group) is not AppGroup and group.name.startswith(AppGroup.APP_GROUP_NAME_PREFIX):
        raise HTTPException(
            400, "The App- prefix cannot be used for non-app groups. Please choose a different group name."
        )
    if type(group) is not RoleGroup and group.name.startswith(RoleGroup.ROLE_GROUP_NAME_PREFIX):
        raise HTTPException(
            400, "The Role- prefix cannot be used for non-role groups. Please choose a different group name."
        )
    if type(group) is AppGroup and group.name.endswith(
        f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    ):
        raise HTTPException(
            400, "App owner groups cannot be created directly. They are created automatically when an app is created."
        )

    created = CreateGroup(group=group, tags=body.get("tags_to_add", []), current_user_id=current_user_id).execute()
    refreshed = _load_group_with_options(db, created.id)
    return safe_dump(_group_adapter, refreshed)


@router.put("/{group_id}", name="group_by_id_put")
def put_group(
    group_id: str,
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    db: DbSession = None,  # type: ignore[assignment]
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    from api.plugins.app_group_lifecycle import (
        get_app_group_lifecycle_plugin_to_invoke,
        validate_app_group_lifecycle_plugin_group_config,
    )

    body = body or {}
    group = _load_group_with_options(db, group_id)
    if group is None:
        raise HTTPException(404, "Not Found")
    if "description" in body:
        body["description"] = _validate_description(body["description"], True)

    # Validate plugin_data for app groups against the configured plugin's schema.
    if "plugin_data" in body and isinstance(group, AppGroup):
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(group)
        if plugin_id is not None:
            try:
                errors = validate_app_group_lifecycle_plugin_group_config(body["plugin_data"], plugin_id)
            except ValueError as e:
                raise HTTPException(400, f"plugin_data: {e}") from e
            if errors:
                raise HTTPException(400, f"plugin_data: {errors}")

    if not _perms.can_manage_group(db, current_user_id, group):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")

    new_plugin_data = body.get("plugin_data") if "plugin_data" in body else None
    if new_plugin_data is not None and new_plugin_data != (group.plugin_data or {}):
        if not (
            is_access_admin(db, current_user_id)
            or (type(group) is AppGroup and is_app_owner_group_owner(db, current_user_id, app_group=group))
        ):
            raise HTTPException(
                403, "Only Access owners and app owners are allowed to configure plugins at the group level"
            )

    if "tags_to_remove" in body and len(body["tags_to_remove"]) > 0:
        if not is_access_admin(db, current_user_id):
            raise HTTPException(
                403, "Current user is not an Access Admin and not allowed to remove tags from this group"
            )

    # Prevent rebinding an AppGroup to a different app without owning the
    # target app. Access admins can always rebind. Apply the rebind here when
    # the group type isn't also changing — type-change rebinds are wired into
    # ModifyGroupType below.
    if type(group) is AppGroup and "app_id" in body and body["app_id"] != group.app_id:
        from api.models import App

        target_app = db.query(App).filter(App.id == body["app_id"]).filter(App.deleted_at.is_(None)).first()
        if target_app is None:
            raise HTTPException(404, "Not Found")
        if not (
            _perms.is_access_admin(db, current_user_id)
            or _perms.is_app_owner_group_owner(db, current_user_id, app=target_app)
        ):
            raise HTTPException(
                403, "Current user is not an app owner of the target app and not allowed to reassign this group"
            )
        if not body.get("type") or body["type"] == group.type:
            group.app_id = body["app_id"]

    # App owner groups: only tag changes allowed
    if type(group) is AppGroup and group.is_owner:
        if len(body.get("tags_to_add", [])) > 0 or len(body.get("tags_to_remove", [])) > 0:
            ModifyGroupTags(
                group=group,
                tags_to_add=body.get("tags_to_add", []),
                tags_to_remove=body.get("tags_to_remove", []),
                current_user_id=current_user_id,
            ).execute()
            refreshed = _load_group_with_options(db, group.id)
            return safe_dump(_group_adapter, refreshed)
        raise HTTPException(400, "Only tags can be modifed for application owner groups")

    # Block renaming to a reserved prefix unless the final group type matches.
    # Computed using the target type when the request also changes type, since a
    # legitimate OktaGroup→AppGroup/RoleGroup conversion sends both the new name
    # and new type in the same request.
    if "name" in body:
        body_type = body.get("type")
        type_klass_map = {"okta_group": OktaGroup, "role_group": RoleGroup, "app_group": AppGroup}
        final_type = type_klass_map.get(body_type) if body_type else type(group)
        new_name = body["name"]
        if new_name.startswith(AppGroup.APP_GROUP_NAME_PREFIX) and final_type is not AppGroup:
            raise HTTPException(
                400, "The App- prefix cannot be used for non-app groups. Please choose a different group name."
            )
        if new_name.startswith(RoleGroup.ROLE_GROUP_NAME_PREFIX) and final_type is not RoleGroup:
            raise HTTPException(
                400, "The Role- prefix cannot be used for non-role groups. Please choose a different group name."
            )

    try:
        ModifyGroupDetails(
            group=group,
            name=body.get("name") if "name" in body else None,
            description=body.get("description") if "description" in body else None,
        ).execute()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    body_type = body.get("type")
    if body_type and body_type != group.type:
        if not is_access_admin(db, current_user_id):
            raise HTTPException(403, "Current user is not an Access admin and not allowed to change group types")
        type_klass = {"okta_group": OktaGroup, "role_group": RoleGroup, "app_group": AppGroup}.get(body_type)
        if type_klass is not None:
            new_group = type_klass()
            for k in ("name", "description", "is_managed"):
                setattr(new_group, k, getattr(group, k, None))
            if isinstance(new_group, AppGroup):
                new_group.app_id = body.get("app_id", getattr(group, "app_id", None))
                new_group.is_owner = bool(body.get("is_owner", False))
            try:
                group = ModifyGroupType(group=group, group_changes=new_group, current_user_id=current_user_id).execute()
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

    old_plugin_data_for_audit = copy.deepcopy(group.plugin_data) if group.plugin_data else {}
    old_plugin_data = group.plugin_data
    if new_plugin_data is not None:
        group.plugin_data = new_plugin_data
        if old_plugin_data and group.plugin_data != old_plugin_data:
            for key in old_plugin_data:
                if key not in group.plugin_data:
                    group.plugin_data[key] = old_plugin_data[key]
            if type(group) is AppGroup:
                merge_app_lifecycle_plugin_data(group, old_plugin_data)
    db.commit()

    ModifyGroupTags(
        group=group,
        tags_to_add=body.get("tags_to_add", []),
        tags_to_remove=body.get("tags_to_remove", []),
        current_user_id=current_user_id,
    ).execute()

    refreshed = _load_group_with_options(db, group.id)

    # Audit log — plugin configuration changes at the group level
    if old_plugin_data_for_audit != (refreshed.plugin_data or {}):
        from api.context import get_request_context
        from api.models import OktaUser
        from api.schemas import AuditLogSchema, EventType
        import logging as _logging

        _ctx = get_request_context()
        email = getattr(db.get(OktaUser, current_user_id), "email", None) if current_user_id is not None else None
        _logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_modify_plugin,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": current_user_id,
                    "current_user_email": email,
                    "group": refreshed,
                    "old_plugin_data": old_plugin_data_for_audit,
                }
            )
        )

    return safe_dump(_group_adapter, refreshed)


@router.delete("/{group_id}", name="group_by_id_delete")
def delete_group(group_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    group = _load_group_with_options(db, group_id)
    if group is None:
        raise HTTPException(404, "Not Found")
    if not _perms.can_manage_group(db, current_user_id, group):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")
    if type(group) is AppGroup and group.is_owner:
        raise HTTPException(400, "Application owner groups cannot be deleted without first deleting the application")
    DeleteGroup(group=group, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True).model_dump()


@router.get("/{group_id}/audit", name="group_audit_by_id")
def get_group_audit(group_id: str, request: Request, current_user_id: CurrentUserId) -> RedirectResponse:
    from urllib.parse import urlencode

    qp = dict(request.query_params)
    qp["group_id"] = group_id
    return RedirectResponse(url=f"/api/audit/users?{urlencode(qp)}", status_code=307)


@router.get("/{group_id}/members", name="group_members_by_id")
def get_group_members(group_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    group = (
        db.query(OktaGroup)
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(_db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
        .first()
    )
    if group is None:
        raise HTTPException(404, "Not Found")

    base_query = (
        db.query(OktaUserGroupMember)
        .with_entities(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
        .filter(
            _db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > _db.func.now(),
            )
        )
        .filter(OktaUserGroupMember.group_id == group.id)
        .group_by(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
    )
    rows = base_query.all()
    return {
        "members": [r.user_id for r in rows if not r.is_owner],
        "owners": [r.user_id for r in rows if r.is_owner],
    }


@router.put("/{group_id}/members", name="group_members_by_id_put")
def put_group_members(
    group_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    body: GroupMember | None = None,
) -> dict[str, Any]:
    group = (
        db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
        .filter(OktaGroup.deleted_at.is_(None))
        .filter(_db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
        .first()
    )
    if group is None:
        raise HTTPException(404, "Not Found")
    if body is None:
        raise HTTPException(400, "Request body is required")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")

    if not _perms.can_manage_group(db, current_user_id, group):
        if (
            len(body.members_to_add) > 0
            or len(body.owners_to_add) > 0
            or len(body.members_should_expire) > 0
            or len(body.owners_should_expire) > 0
        ):
            raise HTTPException(403, "Current user is not allowed to modify user access to this group")
        for user_id in body.members_to_remove + body.owners_to_remove:
            if user_id != current_user_id:
                raise HTTPException(403, "Current user is not allowed to perform this action")

    valid, err_message = CheckForSelfAdd(
        group=group,
        current_user=current_user_id,
        members_to_add=body.members_to_add,
        owners_to_add=body.owners_to_add,
    ).execute_for_group()
    if not valid:
        raise HTTPException(400, err_message)

    valid, err_message = CheckForReason(
        group=group,
        reason=body.created_reason or "",
        members_to_add=body.members_to_add,
        owners_to_add=body.owners_to_add,
    ).execute_for_group()
    if not valid:
        raise HTTPException(400, err_message)

    ModifyGroupUsers(
        group=group,
        current_user_id=current_user_id,
        users_added_ended_at=body.users_added_ending_at,
        members_to_add=body.members_to_add,
        owners_to_add=body.owners_to_add,
        members_should_expire=body.members_should_expire,
        owners_should_expire=body.owners_should_expire,
        members_to_remove=body.members_to_remove,
        owners_to_remove=body.owners_to_remove,
        created_reason=body.created_reason or "",
    ).execute()

    return get_group_members(group_id, db, current_user_id)
