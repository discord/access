"""Apps router."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.auth.permissions import (
    require_access_admin_or_app_creator,
    require_app_owner_or_access_admin_for_app,
)
from api.database import DbSession
from api.models import (
    App,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
)
from fastapi_pagination.ext.sqlalchemy import apaginate

from api.pagination import AppGroupsPage, Page, validated
from api.schemas import (
    AppSummary,
    AppDetail,
    AppGroupForAppDetail,
    CreateAppBody,
    DeleteMessage,
    SearchAppQuery,
    UpdateAppBody,
)


APP_LOAD_OPTIONS = (
    selectinload(App.active_app_tags).options(
        joinedload(AppTagMap.active_tag),
        joinedload(AppTagMap.active_app),
    ),
)


router = APIRouter(prefix="/api/apps", tags=["apps"])


@router.get("", name="apps")
async def list_apps(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchAppQuery, Query()],
) -> Page[AppSummary]:
    stmt = select(App).where(App.deleted_at.is_(None)).order_by(func.lower(App.name))
    if q_args.q:
        like = f"%{q_args.q}%"
        stmt = stmt.where(or_(App.name.ilike(like), App.description.ilike(like)))
    return await apaginate(db, stmt, transformer=validated(AppSummary))


@router.get("/{app_id}", name="app_by_id")
async def get_app(app_id: str, db: DbSession, current_user_id: CurrentUserId) -> AppDetail:
    app = (
        await db.scalars(
            select(App)
            .options(*APP_LOAD_OPTIONS)
            .where(App.deleted_at.is_(None))
            .where(or_(App.id == app_id, App.name == app_id))
        )
    ).first()
    if app is None:
        raise HTTPException(404, "Not Found")
    return AppDetail.model_validate(app, from_attributes=True)


@router.get("/{app_id}/groups", name="app_groups_by_id")
async def get_app_groups(
    app_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    owner: Annotated[Optional[bool], Query()] = None,
    q: Annotated[Optional[str], Query()] = None,
) -> AppGroupsPage[AppGroupForAppDetail]:
    """Paginated app-groups for an app, owners first then by name. Members are
    NOT inlined — each item carries `member_count` / `owner_count` only, so the
    response is bounded by the number of groups on the page regardless of how
    many members any single group has. The UI fetches a group's members on
    demand from `GET /api/groups/{id}/member-details`.

    `owner` filters to owner / non-owner app-groups. `q` is the app page's
    search: it filters to groups whose own name matches the query, or that have
    an active member matching by name or email, computed in SQL so it doesn't
    need every member client-side."""
    resolved_app_id = (
        await db.scalars(
            select(App.id).where(App.deleted_at.is_(None)).where(or_(App.id == app_id, App.name == app_id))
        )
    ).first()
    if resolved_app_id is None:
        raise HTTPException(404, "Not Found")
    stmt = (
        select(AppGroup)
        .options(joinedload(AppGroup.app))
        .where(AppGroup.app_id == resolved_app_id)
        .where(AppGroup.deleted_at.is_(None))
    )
    if owner is not None:
        stmt = stmt.where(AppGroup.is_owner.is_(owner))
    if q:
        like = f"%{q}%"
        member_match = (
            select(OktaUserGroupMember.id)
            .join(OktaUser, OktaUser.id == OktaUserGroupMember.user_id)
            .where(OktaUserGroupMember.group_id == AppGroup.id)
            .where(OktaUser.deleted_at.is_(None))
            .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
            .where(
                or_(
                    OktaUser.email.ilike(like),
                    OktaUser.display_name.ilike(like),
                    OktaUser.first_name.ilike(like),
                    OktaUser.last_name.ilike(like),
                )
            )
            .exists()
        )
        stmt = stmt.where(or_(AppGroup.name.ilike(like), member_match))
    # AppGroup.id is a unique final tiebreaker so rows that tie on
    # (is_owner, lower(name)) have a stable order across LIMIT/OFFSET pages.
    stmt = stmt.order_by(AppGroup.is_owner.desc(), func.lower(AppGroup.name), AppGroup.id)

    async def _with_counts(groups: Any) -> list[Any]:
        ids = [g.id for g in groups]
        counts: dict[tuple[str, bool], int] = {}
        if ids:
            rows = (
                await db.execute(
                    select(OktaUserGroupMember.group_id, OktaUserGroupMember.is_owner, func.count())
                    .where(OktaUserGroupMember.group_id.in_(ids))
                    .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
                    .group_by(OktaUserGroupMember.group_id, OktaUserGroupMember.is_owner)
                )
            ).all()
            for gid, is_owner, count in rows:
                counts[(gid, is_owner)] = count
        # Stash the aggregates on the ORM instances so `from_attributes` picks
        # them up (these are plain, non-mapped attributes).
        for g in groups:
            g.member_count = counts.get((g.id, False), 0)
            g.owner_count = counts.get((g.id, True), 0)
        return validated(AppGroupForAppDetail)(groups)

    return await apaginate(db, stmt, transformer=_with_counts)


@router.post("", name="apps_create", status_code=201)
async def post_app(
    body: CreateAppBody,
    db: DbSession,
    current_user_id: str = Depends(require_access_admin_or_app_creator),
) -> AppDetail:
    from api.models import AppGroup as _AppGroup, OktaUser, RoleGroup
    from api.operations import CreateApp

    description = body.description if body.description is not None else ""

    # Reject duplicates by name.
    existing = (
        await db.scalars(
            select(App).where(func.lower(App.name) == func.lower(body.name)).where(App.deleted_at.is_(None))
        )
    ).first()
    if existing is not None:
        raise HTTPException(400, "App already exists with the same name")

    # Default owner = current user; explicit initial_owner_id wins.
    owner_id: str | None = None
    if await db.get(OktaUser, current_user_id) is not None:
        owner_id = current_user_id
    if body.initial_owner_id is not None:
        owner = (
            await db.scalars(
                select(OktaUser)
                .where(OktaUser.deleted_at.is_(None))
                .where(
                    or_(
                        OktaUser.id == body.initial_owner_id,
                        OktaUser.email.ilike(body.initial_owner_id),
                    )
                )
            )
        ).first()
        if owner is None:
            raise HTTPException(400, "Given App initial_owner_id is not a valid user")
        owner_id = owner.id

    if owner_id is None:
        raise HTTPException(400, "App initial_owner_id is required")

    owner_role_ids: list[str] = []
    if body.initial_owner_role_ids is not None:
        roles = (
            await db.scalars(
                select(RoleGroup)
                .where(RoleGroup.id.in_(body.initial_owner_role_ids))
                .where(RoleGroup.deleted_at.is_(None))
            )
        ).all()
        owner_role_ids = [r.id for r in roles]
        if len(owner_role_ids) != len(body.initial_owner_role_ids):
            raise HTTPException(400, "Given App initial_owner_role_ids contains invalid role ids")

    name = body.name
    app_group_prefix = f"{_AppGroup.APP_GROUP_NAME_PREFIX}{name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    owner_group_name = f"{app_group_prefix}{_AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"

    # If an owner-named group already exists *with owners*, refuse to create
    # the app — otherwise CreateApp would silently absorb that group and its
    # owners, which is confusing if a previous owner was squatting on the
    # name. An empty pre-existing group, or one with members but no owners,
    # is fine to absorb.
    from sqlalchemy.orm import with_polymorphic

    poly_group = with_polymorphic(OktaGroup, [_AppGroup, RoleGroup])
    existing_owner_group = (
        await db.scalars(
            select(poly_group)
            .options(selectinload(poly_group.active_user_ownerships))
            .where(func.lower(OktaGroup.name) == func.lower(owner_group_name))
            .where(OktaGroup.deleted_at.is_(None))
        )
    ).first()
    if existing_owner_group is not None and len(existing_owner_group.active_user_ownerships) > 0:
        raise HTTPException(
            409,
            f"An owner group with existing owners already exists for this app name. Select a different app"
            f" name, change the name of {owner_group_name}, or remove the existing owners of"
            f" {owner_group_name} to be able to proceed.",
        )

    initial_additional_app_groups: list[dict[str, Any]] = [
        ig.model_dump(exclude_none=True) for ig in (body.initial_additional_app_groups or [])
    ]

    app_obj = App(name=name, description=description)
    created = await CreateApp(
        app=app_obj,
        owner_id=owner_id,
        owner_role_ids=owner_role_ids,
        additional_app_groups=initial_additional_app_groups,
        tags=body.tags_to_add or [],
        current_user_id=current_user_id,
    ).execute()
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    created_id = created.id
    db.expire_all()
    refreshed = (await db.scalars(select(App).options(*APP_LOAD_OPTIONS).where(App.id == created_id))).first()
    return AppDetail.model_validate(refreshed, from_attributes=True)


@router.put("/{app_id}", name="app_by_id_put")
async def put_app(
    app_id: str,
    body: UpdateAppBody,
    db: DbSession,
    current_user_id: CurrentUserId,
    app_obj=Depends(require_app_owner_or_access_admin_for_app),
) -> AppDetail:
    import copy

    from api.auth.permissions import is_access_admin
    from api.models import AppGroup as _AppGroup
    from api.models.app_group import app_owners_group_description
    from api.operations import ModifyAppTags, ModifyGroupDetails
    from api.plugins.app_group_lifecycle import merge_app_lifecycle_plugin_data

    from api.plugins.app_group_lifecycle import validate_app_group_lifecycle_plugin_app_config

    fields_set = body.model_fields_set
    description = (body.description if body.description is not None else "") if "description" in fields_set else None

    # Snapshot the old plugin state before any mutation.
    old_app_group_lifecycle_plugin = app_obj.app_group_lifecycle_plugin
    old_plugin_data_for_audit_pre = copy.deepcopy(app_obj.plugin_data) if app_obj.plugin_data else {}

    # Validate plugin_data against the configured plugin's schema before mutating.
    new_plugin_id = (
        body.app_group_lifecycle_plugin
        if "app_group_lifecycle_plugin" in fields_set
        else app_obj.app_group_lifecycle_plugin
    )
    if "plugin_data" in fields_set and new_plugin_id is not None:
        try:
            errors = validate_app_group_lifecycle_plugin_app_config(body.plugin_data, new_plugin_id)
        except ValueError as e:
            raise HTTPException(400, f"plugin_data: {e}") from e
        if errors:
            raise HTTPException(400, f"plugin_data: {errors}")

    # Plugin config changes require access admin.
    new_plugin = (
        body.app_group_lifecycle_plugin
        if "app_group_lifecycle_plugin" in fields_set
        else app_obj.app_group_lifecycle_plugin
    )
    new_plugin_data = body.plugin_data if "plugin_data" in fields_set else None
    if (
        new_plugin != app_obj.app_group_lifecycle_plugin
        or (new_plugin_data is not None and new_plugin_data != (app_obj.plugin_data or {}))
    ) and not await is_access_admin(db, current_user_id):
        raise HTTPException(403, "Only Access owners are allowed to configure plugins at the app level")

    # Reject duplicate name on rename
    new_name = body.name
    if new_name and new_name.lower() != app_obj.name.lower():
        existing = (
            await db.scalars(
                select(App).where(func.lower(App.name) == func.lower(new_name)).where(App.deleted_at.is_(None))
            )
        ).first()
        if existing is not None:
            raise HTTPException(400, "App already exists with the same name")

    # tags_to_remove gated on admin
    if body.tags_to_remove and len(body.tags_to_remove) > 0:
        if not await is_access_admin(db, current_user_id):
            raise HTTPException(403, "Current user is not an Access Admin and not allowed to remove tags from this app")

    tags_to_add = body.tags_to_add or []
    tags_to_remove = body.tags_to_remove or []

    # Built-in Access app: only tags can be modified
    if app_obj.name == App.ACCESS_APP_RESERVED_NAME:
        if len(tags_to_add) > 0 or len(tags_to_remove) > 0:
            await ModifyAppTags(
                app=app_obj,
                tags_to_add=tags_to_add,
                tags_to_remove=tags_to_remove,
                current_user_id=current_user_id,
            ).execute()
            # Drop cached ORM state so the response reflects what the operation
            # committed (expire_on_commit=False keeps pre-operation state otherwise).
            app_id = app_obj.id
            db.expire_all()
            refreshed = (await db.scalars(select(App).options(*APP_LOAD_OPTIONS).where(App.id == app_id))).first()
            return AppDetail.model_validate(refreshed, from_attributes=True)
        raise HTTPException(400, "Only tags can be modified for the Access application")

    old_app_name = app_obj.name
    old_plugin_data = app_obj.plugin_data

    if body.name is not None:
        app_obj.name = body.name
    if description is not None:
        app_obj.description = description
    if "app_group_lifecycle_plugin" in fields_set:
        app_obj.app_group_lifecycle_plugin = body.app_group_lifecycle_plugin
    if new_plugin_data is not None:
        app_obj.plugin_data = new_plugin_data
        if old_plugin_data and app_obj.plugin_data != old_plugin_data:
            for key in old_plugin_data:
                if key not in app_obj.plugin_data:
                    app_obj.plugin_data[key] = old_plugin_data[key]
            merge_app_lifecycle_plugin_data(app_obj, old_plugin_data)

    # Rename associated app groups when the app name changed
    if app_obj.name != old_app_name:
        old_prefix = f"{_AppGroup.APP_GROUP_NAME_PREFIX}{old_app_name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        new_prefix = f"{_AppGroup.APP_GROUP_NAME_PREFIX}{app_obj.name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        app_groups = (await db.scalars(select(_AppGroup).where(_AppGroup.app_id == app_obj.id))).all()
        for ag in app_groups:
            if ag.name.startswith(old_prefix):
                suffix = ag.name[len(old_prefix) :]
                new_group_name = f"{new_prefix}{suffix}"
            else:
                new_group_name = f"{new_prefix}{ag.name}"
            new_description = app_owners_group_description(app_obj.name) if ag.is_owner else None
            await ModifyGroupDetails(group=ag, name=new_group_name, description=new_description).execute()

    await db.commit()

    await ModifyAppTags(
        app=app_obj,
        tags_to_add=tags_to_add,
        tags_to_remove=tags_to_remove,
        current_user_id=current_user_id,
    ).execute()

    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    # Capture the pk first: reading it after expire_all() would lazy-load
    # synchronously, which raises MissingGreenlet on an AsyncSession.
    app_id = app_obj.id
    db.expire_all()
    refreshed = (await db.scalars(select(App).options(*APP_LOAD_OPTIONS).where(App.id == app_id))).first()

    # Audit logging — both name renames and plugin assignment/configuration
    # changes.
    name_changed = old_app_name.lower() != app_obj.name.lower()
    plugin_changed = old_app_group_lifecycle_plugin != getattr(
        refreshed, "app_group_lifecycle_plugin", None
    ) or old_plugin_data_for_audit_pre != (refreshed.plugin_data or {})

    if name_changed or plugin_changed:
        from api.context import get_request_context
        from api.models import OktaUser
        from api.schemas import AuditLogSchema, EventType
        import logging as _logging

        _ctx = get_request_context()
        email = getattr(await db.get(OktaUser, current_user_id), "email", None) if current_user_id is not None else None
        audit_logger = _logging.getLogger("access.audit")

        if name_changed:
            audit_logger.info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.app_modify_name,
                        "user_agent": _ctx.user_agent if _ctx else None,
                        "ip": _ctx.ip if _ctx else None,
                        "current_user_id": current_user_id,
                        "current_user_email": email,
                        "app": refreshed,
                        "old_app_name": old_app_name,
                    }
                )
            )

        if plugin_changed:
            audit_logger.info(
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
    return AppDetail.model_validate(refreshed, from_attributes=True)


@router.delete("/{app_id}", name="app_by_id_delete")
async def delete_app(
    app_id: str,
    db: DbSession,
    app_obj=Depends(require_app_owner_or_access_admin_for_app),
    current_user_id: CurrentUserId = None,  # type: ignore[assignment]
) -> DeleteMessage:
    from api.operations import DeleteApp

    # The reserved Access app underpins admin auth — deleting it would brick
    # the app, so refuse outright.
    if app_obj.name == App.ACCESS_APP_RESERVED_NAME:
        raise HTTPException(400, "The Access Application cannot be deleted")

    await DeleteApp(app=app_obj, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True)
