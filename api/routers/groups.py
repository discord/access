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

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import and_, func, nullsfirst, or_, select
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic
from starlette.requests import Request

from api.auth import permissions as _perms
from api.auth.dependencies import CurrentUserId
from api.auth.permissions import (
    is_access_admin,
    is_app_owner_group_owner,
)
from api.database import DbSession
from api.models import App, AppGroup, OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.operations import (
    CreateGroup,
    DeleteGroup,
    ModifyGroupDetails,
    ModifyGroupPluginData,
    ModifyGroupTags,
    ModifyGroupType,
    ModifyGroupUsers,
)
from api.operations.constraints import CheckForReason, CheckForSelfAdd
from fastapi_pagination.ext.sqlalchemy import apaginate

from api.pagination import Page, validated
from api.routers._eager import (
    bind_role_group_map_own_groups,
    group_tag_map_options,
    role_group_map_options,
    role_group_map_options_for_own_group,
    user_group_member_options,
)
from api.routers._fan_out import defer_fan_out
from api.schemas import (
    GroupSummary,
    CreateGroupBody,
    DeleteMessage,
    GroupDetail,
    GroupMembersSummary,
    OktaUserGroupMemberDetail,
    SearchGroupQuery,
    UpdateGroupBody,
)
from api.schemas.requests_schemas import (
    GroupMember,
    _AppGroupCreateBody,
    _AppGroupUpdateBody,
    _RoleGroupCreateBody,
)

import copy

router = APIRouter(prefix="/api/groups", tags=["groups"], dependencies=[Depends(defer_fan_out)])

ROLE_ASSOCIATED_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup])

DEFAULT_LOAD_OPTIONS = (
    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
    selectinload(OktaGroup.active_role_member_mappings).options(*role_group_map_options_for_own_group()),
    selectinload(OktaGroup.active_role_owner_mappings).options(*role_group_map_options_for_own_group()),
    selectinload(RoleGroup.active_role_associated_group_member_mappings).options(*role_group_map_options()),
    selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(*role_group_map_options()),
    joinedload(AppGroup.app),
    selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
)

# `GroupDetail` is a discriminated union (`Annotated[Union[...], Field(discriminator="type")]`),
# so `model_validate(...)` isn't available — we go through a `TypeAdapter`. The
# adapter is cached at module level since constructing it is non-trivial and
# every detail-shaped response goes through it.
_group_adapter: TypeAdapter[Any] = TypeAdapter(GroupDetail)


async def _load_group_with_options(db: DbSession, group_id: str) -> OktaGroup | None:
    # Routes call this after operations mutate the group; with
    # expire_on_commit=False the identity map would otherwise serve
    # pre-operation relationship state, so drop cached ORM state first.
    db.expire_all()
    group = (
        await db.scalars(
            select(OktaGroup)
            .options(*DEFAULT_LOAD_OPTIONS)
            .where(or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
        )
    ).first()
    if group is not None:
        bind_role_group_map_own_groups(group)
    return group


def _validate_group_plugin_config(
    plugin_data: dict[str, Any],
    app_plugin_data: dict[str, Any],
    plugin_id: str | None,
    old_plugin_data: dict[str, Any] | None = None,
) -> None:
    """Validate group-level plugin_data against the configured plugin's schema, raising
    HTTP 400 on invalid config. No-op when the app has no app group lifecycle plugin.

    Callers resolve the plugin id and the owning app's plugin_data themselves, since
    group-create and group-update obtain them differently (a freshly-built group can't
    lazy-load its app). On update, callers also pass the existing group's plugin_data as
    `old_plugin_data` so the host can reject changes to immutable config fields."""
    if plugin_id is None:
        return

    from api.plugins.app_group_lifecycle import (
        AppGroupLifecyclePluginFilteringError,
        validate_app_group_lifecycle_plugin_group_config,
    )

    try:
        errors = validate_app_group_lifecycle_plugin_group_config(
            plugin_data, plugin_id, app_plugin_data, old_plugin_data=old_plugin_data
        )
    except ValueError as e:
        raise HTTPException(400, f"plugin_data: {e}") from e
    except AppGroupLifecyclePluginFilteringError as e:
        # A plugin that doesn't answer this hook with exactly one response is a server-side
        # misconfiguration, not bad client input, so surface a clear 500 rather than letting the
        # plain Exception become an unhandled stack trace.
        raise HTTPException(500, f"Misconfigured app group lifecycle plugin '{plugin_id}': {e}") from e
    if errors:
        raise HTTPException(400, f"plugin_data: {errors}")


@router.get("", name="groups")
async def list_groups(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchGroupQuery, Query()],
) -> Page[GroupSummary]:
    stmt = (
        select(OktaGroup)
        .options(
            selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
            selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
            # `RoleGroupSummary` exposes `active_role_associated_group_*_mappings`,
            # so the list view needs these eager-loaded for role rows.
            selectinload(RoleGroup.active_role_associated_group_member_mappings).options(*role_group_map_options()),
            selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(*role_group_map_options()),
            joinedload(AppGroup.app),
        )
        .where(OktaGroup.deleted_at.is_(None))
        .order_by(func.lower(OktaGroup.name))
    )
    if q_args.q:
        like = f"%{q_args.q}%"
        stmt = stmt.where(or_(OktaGroup.name.ilike(like), OktaGroup.description.ilike(like)))
    if q_args.managed is not None:
        stmt = stmt.where(OktaGroup.is_managed == q_args.managed)

    return await apaginate(db, stmt, transformer=validated(GroupSummary))


@router.get("/{group_id}", name="group_by_id")
async def get_group(group_id: str, db: DbSession, current_user_id: CurrentUserId) -> GroupDetail:
    group = await _load_group_with_options(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _group_adapter.validate_python(group, from_attributes=True)


@router.post("", name="groups_create", status_code=201)
async def post_group(
    request: Request,
    body: CreateGroupBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> GroupDetail:
    """Create a new group. Body is the polymorphic group input + tags_to_add."""
    description = body.description or ""

    if isinstance(body, _AppGroupCreateBody):
        group: OktaGroup = AppGroup(
            name=body.name,
            description=description,
            app_id=body.app_id,
            is_owner=False,
            plugin_data=body.plugin_data or {},
        )
    elif isinstance(body, _RoleGroupCreateBody):
        group = RoleGroup(name=body.name, description=description)
    else:
        group = OktaGroup(name=body.name, description=description)

    if not (
        await is_access_admin(db, current_user_id)
        or await is_app_owner_group_owner(db, current_user_id, app_group=group if isinstance(group, AppGroup) else None)
    ):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    existing = (
        await db.scalars(
            select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .where(func.lower(OktaGroup.name) == func.lower(group.name))
            .where(OktaGroup.deleted_at.is_(None))
        )
    ).first()
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

    if isinstance(group, AppGroup) and group.plugin_data:
        # group.app uses lazy="raise_on_sql" and the group isn't in the session yet,
        # so load the App directly to resolve the plugin id (mirrors put_group's pattern).
        _app = (await db.scalars(select(App).where(App.id == group.app_id).where(App.deleted_at.is_(None)))).first()
        plugin_id = _app.app_group_lifecycle_plugin if _app is not None else None
        app_plugin_data = _app.plugin_data if _app is not None else {}
        _validate_group_plugin_config(group.plugin_data, app_plugin_data, plugin_id)

    try:
        created = await CreateGroup(group=group, tags=body.tags_to_add or [], current_user_id=current_user_id).execute()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    refreshed = await _load_group_with_options(db, created.id)
    return _group_adapter.validate_python(refreshed, from_attributes=True)


@router.put("/{group_id}", name="group_by_id_put")
async def put_group(
    group_id: str,
    request: Request,
    body: UpdateGroupBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> GroupDetail:
    from api.plugins.app_group_lifecycle import (
        AppGroupLifecycleHook,
        get_app_group_lifecycle_plugin_to_invoke,
        invoke_app_group_lifecycle_hook,
    )

    fields_set = body.model_fields_set
    group = await _load_group_with_options(db, group_id)
    if group is None:
        raise HTTPException(404, "Not Found")

    # Capture the pre-update name/description so the single, consolidated group_updated fire
    # (below) can report them.
    old_name = group.name
    old_description = group.description or ""
    # Length + REQUIRE_DESCRIPTIONS-when-set are enforced by the schema; this
    # just normalises a `None` (only possible when the client explicitly sent
    # `null`) to an empty string for ModifyGroupDetails.
    description = (body.description or "") if "description" in fields_set else None

    # Validate plugin_data for app groups against the configured plugin's schema. The
    # group is loaded with its app (joinedload), so the app's plugin config is available.
    if isinstance(body, _AppGroupUpdateBody) and "plugin_data" in fields_set and isinstance(group, AppGroup):
        app_plugin_data = group.app.plugin_data if group.app is not None else {}
        _validate_group_plugin_config(
            body.plugin_data or {},
            app_plugin_data,
            get_app_group_lifecycle_plugin_to_invoke(group),
            old_plugin_data=group.plugin_data or {},
        )

    if not await _perms.can_manage_group(db, current_user_id, group):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")

    new_plugin_data = (
        body.plugin_data if isinstance(body, _AppGroupUpdateBody) and "plugin_data" in fields_set else None
    )
    if new_plugin_data is not None and new_plugin_data != (group.plugin_data or {}):
        if not (
            await is_access_admin(db, current_user_id)
            or (type(group) is AppGroup and await is_app_owner_group_owner(db, current_user_id, app_group=group))
        ):
            raise HTTPException(
                403, "Only Access owners and app owners are allowed to configure plugins at the group level"
            )

    if body.tags_to_remove and len(body.tags_to_remove) > 0:
        if not await is_access_admin(db, current_user_id):
            raise HTTPException(
                403, "Current user is not an Access Admin and not allowed to remove tags from this group"
            )

    # Prevent rebinding an AppGroup to a different app without owning the
    # target app. Access admins can always rebind. Apply the rebind here when
    # the group type isn't also changing — type-change rebinds are wired into
    # ModifyGroupType below.
    if (
        type(group) is AppGroup
        and isinstance(body, _AppGroupUpdateBody)
        and "app_id" in fields_set
        and body.app_id != group.app_id
    ):
        target_app = (
            await db.scalars(select(App).where(App.id == body.app_id).where(App.deleted_at.is_(None)))
        ).first()
        if target_app is None:
            raise HTTPException(404, "Not Found")
        if not (
            await _perms.is_access_admin(db, current_user_id)
            or await _perms.is_app_owner_group_owner(db, current_user_id, app=target_app)
        ):
            raise HTTPException(
                403, "Current user is not an app owner of the target app and not allowed to reassign this group"
            )
        if body.type == group.type:
            # target_app was fetched by `App.id == body.app_id`, so its id is
            # the same non-null value — assigning it keeps `app_id: Mapped[str]`
            # non-optional.
            group.app_id = target_app.id

    tags_to_add = body.tags_to_add or []
    tags_to_remove = body.tags_to_remove or []

    # App owner groups: only tag changes allowed
    if type(group) is AppGroup and group.is_owner:
        if len(tags_to_add) > 0 or len(tags_to_remove) > 0:
            await ModifyGroupTags(
                group=group,
                tags_to_add=tags_to_add,
                tags_to_remove=tags_to_remove,
                current_user_id=current_user_id,
            ).execute()
            refreshed = await _load_group_with_options(db, group.id)
            return _group_adapter.validate_python(refreshed, from_attributes=True)
        raise HTTPException(400, "Only tags can be modifed for application owner groups")

    # Block renaming to a reserved prefix unless the final group type matches.
    # Computed using the target type since a legitimate
    # OktaGroup→AppGroup/RoleGroup conversion sends both the new name and the
    # new type in the same request.
    if "name" in fields_set and body.name is not None:
        type_klass_map = {"okta_group": OktaGroup, "role_group": RoleGroup, "app_group": AppGroup}
        final_type = type_klass_map[body.type]
        if body.name.startswith(AppGroup.APP_GROUP_NAME_PREFIX) and final_type is not AppGroup:
            raise HTTPException(
                400, "The App- prefix cannot be used for non-app groups. Please choose a different group name."
            )
        if body.name.startswith(RoleGroup.ROLE_GROUP_NAME_PREFIX) and final_type is not RoleGroup:
            raise HTTPException(
                400, "The Role- prefix cannot be used for non-role groups. Please choose a different group name."
            )

    # Converting a group to an app group must yield a name carrying the target
    # app's "App-{app name}-" prefix, whether or not the request also renames.
    # Same-type renames are enforced in ModifyGroupDetails; conversions are
    # checked here because the rename runs while the group is still its old
    # type. Validating before ModifyGroupDetails also keeps an invalid
    # conversion from committing the rename.
    if body.type == "app_group" and type(group) is not AppGroup:
        final_name = body.name if "name" in fields_set and body.name is not None else group.name
        target_app_id = (
            body.app_id
            if isinstance(body, _AppGroupUpdateBody) and "app_id" in fields_set
            else getattr(group, "app_id", None)
        )
        conversion_app = (
            await db.scalars(select(App).where(App.id == target_app_id).where(App.deleted_at.is_(None)))
        ).first()
        if conversion_app is None:
            raise HTTPException(400, "App for AppGroup does not exist")
        app_group_name_prefix = (
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{conversion_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        )
        if not final_name.startswith(app_group_name_prefix):
            raise HTTPException(
                400,
                'App Group name "{}" should be prefixed with App name. For example: "{}"'.format(
                    final_name, app_group_name_prefix
                ),
            )

        # Validate plugin_data against the *target* app's plugin now, on the conversion path.
        # The standalone-update guard below runs only for groups that are already AppGroups, so
        # without this an okta_group -> app_group PUT would persist invalid config (surfacing
        # later as a plugin SYNC_ERROR instead of a 400). old_plugin_data=None: attaching the
        # plugin on conversion is a fresh create, so immutable fields may be set.
        if isinstance(body, _AppGroupUpdateBody) and "plugin_data" in fields_set:
            _validate_group_plugin_config(
                body.plugin_data or {},
                conversion_app.plugin_data or {},
                conversion_app.app_group_lifecycle_plugin,
                old_plugin_data=None,
            )

    try:
        await ModifyGroupDetails(
            group=group,
            name=body.name if "name" in fields_set else None,
            description=description,
            # A type conversion legitimately renames away from the App- prefix;
            # the final-state name rules are enforced above and by ModifyGroupType.
            validate_app_group_prefix=body.type == group.type,
            # Suppress the per-operation fire; put_group fires a single consolidated
            # group_updated below so a combined name + config change reconciles once.
            fire_lifecycle_hook=False,
        ).execute()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    body_type = body.type
    type_changed = body_type != group.type
    if type_changed:
        if not await is_access_admin(db, current_user_id):
            raise HTTPException(403, "Current user is not an Access admin and not allowed to change group types")
        type_klass = {"okta_group": OktaGroup, "role_group": RoleGroup, "app_group": AppGroup}[body_type]
        new_group = type_klass()
        for k in ("name", "description", "is_managed"):
            setattr(new_group, k, getattr(group, k, None))
        if isinstance(new_group, AppGroup) and isinstance(body, _AppGroupUpdateBody):
            resolved_app_id = body.app_id if "app_id" in fields_set else getattr(group, "app_id", None)
            # `app_id` is a non-nullable Mapped[str]; leave it unset (equivalently
            # None on the transient object) when unresolved rather than assigning
            # None, and let ModifyGroupType / the DB reject a missing app_id.
            if resolved_app_id is not None:
                new_group.app_id = resolved_app_id
            new_group.is_owner = False
        try:
            group = await ModifyGroupType(
                group=group,
                group_changes=new_group,
                current_user_id=current_user_id,
                # Defer the group_created fire to the single consolidated fire below, after
                # name/description/plugin_data are applied, so the plugin observes final state.
                fire_created_hook=False,
            ).execute()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    old_plugin_data_for_audit = copy.deepcopy(group.plugin_data) if group.plugin_data else {}
    config_changed = False
    if new_plugin_data is not None:
        # ModifyGroupPluginData owns the merge (preserving omitted plugin entries and
        # per-plugin status). We suppress its per-operation group_updated fire and fire once
        # below instead, so a PUT changing both name and config reconciles once, not twice.
        plugin_op = ModifyGroupPluginData(group=group, plugin_data=new_plugin_data, fire_lifecycle_hook=False)
        await plugin_op.execute()
        config_changed = plugin_op.config_changed
    else:
        await db.commit()

    # Fire exactly one app group lifecycle hook for the whole PUT, after every field is applied so
    # the plugin observes the final state. The per-operation fires of ModifyGroupDetails,
    # ModifyGroupPluginData, and ModifyGroupType's group_created were all suppressed above.
    #   - Converted TO an app group -> group_created (regardless of name/description/config, since
    #     the group is new to the plugin).
    #   - No type change, name/description/config changed -> group_updated.
    # Converting AWAY from an app group already fired group_deleted inside ModifyGroupType (which
    # needs the pre-conversion AppGroup row, so it can't be deferred); the final type has no
    # lifecycle plugin, so nothing is fired here for that case.
    name_or_desc_changed = group.name != old_name or (group.description or "") != old_description
    if type_changed and isinstance(group, AppGroup):
        await invoke_app_group_lifecycle_hook(AppGroupLifecycleHook.GROUP_CREATED, group=group)
    elif not type_changed and (name_or_desc_changed or config_changed):
        await invoke_app_group_lifecycle_hook(
            AppGroupLifecycleHook.GROUP_UPDATED,
            group=group,
            old_name=old_name,
            old_description=old_description,
        )

    await ModifyGroupTags(
        group=group,
        tags_to_add=tags_to_add,
        tags_to_remove=tags_to_remove,
        current_user_id=current_user_id,
    ).execute()

    refreshed = await _load_group_with_options(db, group.id)
    # The group was just committed above, so re-loading it by id always resolves.
    assert refreshed is not None

    # Audit log — plugin configuration changes at the group level
    if old_plugin_data_for_audit != (refreshed.plugin_data or {}):
        from api.context import get_request_context
        from api.models import OktaUser
        from api.schemas import AuditLogSchema, EventType
        import logging as _logging

        _ctx = get_request_context()
        email = getattr(await db.get(OktaUser, current_user_id), "email", None) if current_user_id is not None else None
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

    return _group_adapter.validate_python(refreshed, from_attributes=True)


@router.delete("/{group_id}", name="group_by_id_delete")
async def delete_group(group_id: str, db: DbSession, current_user_id: CurrentUserId) -> DeleteMessage:
    group = await _load_group_with_options(db, group_id)
    if group is None:
        raise HTTPException(404, "Not Found")
    if not await _perms.can_delete_group(db, current_user_id, group):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")
    if type(group) is AppGroup and group.is_owner:
        raise HTTPException(400, "Application owner groups cannot be deleted without first deleting the application")
    await DeleteGroup(group=group, current_user_id=current_user_id).execute()
    return DeleteMessage(deleted=True)


@router.get("/{group_id}/audit", name="group_audit_by_id")
async def get_group_audit(group_id: str, request: Request, current_user_id: CurrentUserId) -> RedirectResponse:
    from urllib.parse import urlencode

    qp = dict(request.query_params)
    qp["group_id"] = group_id
    return RedirectResponse(url=f"/api/audit/users?{urlencode(qp)}", status_code=307)


@router.get("/{group_id}/members", name="group_members_by_id")
async def get_group_members(group_id: str, db: DbSession, current_user_id: CurrentUserId) -> GroupMembersSummary:
    group = (
        await db.scalars(
            select(OktaGroup)
            .where(OktaGroup.deleted_at.is_(None))
            .where(or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
        )
    ).first()
    if group is None:
        raise HTTPException(404, "Not Found")

    base_stmt = (
        select(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
        .where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
        .where(OktaUserGroupMember.group_id == group.id)
        .group_by(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
    )
    rows = (await db.execute(base_stmt)).all()
    return GroupMembersSummary(
        members=[r.user_id for r in rows if not r.is_owner],
        owners=[r.user_id for r in rows if r.is_owner],
    )


@router.get("/{group_id}/member-details", name="group_member_details_by_id")
async def get_group_member_details(
    group_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    owner: Annotated[Optional[bool], Query()] = None,
    q: Annotated[Optional[str], Query()] = None,
) -> Page[OktaUserGroupMemberDetail]:
    """Paginated, fully-hydrated active membership rows for a group. `owner=true`
    returns ownerships, `owner=false` memberships, omitted returns both. Lets the
    group page page through members instead of inlining every row in the group
    detail response.

    Pagination is by distinct *user*, not by membership row: a user can hold
    several active rows for one group (a direct grant plus role-granted ones),
    and the UI renders one row per user. Paging by user keeps `total` aligned
    with the de-duplicated list the UI shows and keeps all of a user's rows on
    the same page. Note `total` and `size` count users, so a page can contain
    more than `size` items (rows) when users hold multiple rows; consumers must
    not assume ``len(items) == size``.

    `q` is the group page's member search: it filters to users whose name or
    email matches the query, computed in SQL so a large paginated group can be
    searched without loading every member client-side."""
    resolved_group_id = (
        await db.scalars(
            select(OktaGroup.id)
            .where(OktaGroup.deleted_at.is_(None))
            .where(or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
        )
    ).first()
    if resolved_group_id is None:
        raise HTTPException(404, "Not Found")

    active = or_(
        OktaUserGroupMember.ended_at.is_(None),
        OktaUserGroupMember.ended_at > func.now(),
    )

    # Page over distinct users ordered by email so the alphabetical list the UI
    # renders is continuous across pages instead of restarting A-Z on each page.
    # Join the active (non-deleted) user both to sort by email and to keep total
    # aligned with the rows that actually render (a member's detail row requires
    # an active user). `email` is in the select list so SELECT DISTINCT can order
    # by it; total is then the distinct user count.
    user_stmt = (
        select(OktaUserGroupMember.user_id, OktaUser.email)
        .join(OktaUser, and_(OktaUser.id == OktaUserGroupMember.user_id, OktaUser.deleted_at.is_(None)))
        .where(OktaUserGroupMember.group_id == resolved_group_id)
        .where(active)
    )
    if owner is not None:
        user_stmt = user_stmt.where(OktaUserGroupMember.is_owner.is_(owner))
    if q:
        like = f"%{q}%"
        user_stmt = user_stmt.where(
            or_(
                OktaUser.email.ilike(like),
                OktaUser.display_name.ilike(like),
                OktaUser.first_name.ilike(like),
                OktaUser.last_name.ilike(like),
            )
        )
    user_stmt = user_stmt.distinct().order_by(OktaUser.email, OktaUserGroupMember.user_id)

    async def _load_rows(user_rows: Any) -> list[Any]:
        user_ids = [r if isinstance(r, str) else r[0] for r in user_rows]
        if not user_ids:
            return []
        detail_stmt = (
            select(OktaUserGroupMember)
            .join(OktaUser, and_(OktaUser.id == OktaUserGroupMember.user_id, OktaUser.deleted_at.is_(None)))
            .options(*user_group_member_options())
            .where(OktaUserGroupMember.group_id == resolved_group_id)
            .where(active)
            .where(OktaUserGroupMember.user_id.in_(user_ids))
        )
        if owner is not None:
            detail_stmt = detail_stmt.where(OktaUserGroupMember.is_owner.is_(owner))
        detail_stmt = detail_stmt.order_by(OktaUser.email, OktaUserGroupMember.user_id, OktaUserGroupMember.id)
        return validated(OktaUserGroupMemberDetail)((await db.scalars(detail_stmt)).all())

    return await apaginate(db, user_stmt, transformer=_load_rows)


@router.put("/{group_id}/members", name="group_members_by_id_put")
async def put_group_members(
    group_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    body: GroupMember | None = None,
) -> GroupMembersSummary:
    group = (
        await db.scalars(
            select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .where(OktaGroup.deleted_at.is_(None))
            .where(or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
        )
    ).first()
    if group is None:
        raise HTTPException(404, "Not Found")
    # Body is `Optional` so the missing-group 404 above fires even when the
    # client sends no body. After the 404 we still require a body for the
    # actual mutation.
    if body is None:
        raise HTTPException(400, "Request body is required")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")

    if not await _perms.can_manage_group(db, current_user_id, group):
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

    valid, err_message = await CheckForSelfAdd(
        group=group,
        current_user=current_user_id,
        members_to_add=body.members_to_add,
        owners_to_add=body.owners_to_add,
    ).execute_for_group()
    if not valid:
        raise HTTPException(400, err_message)

    valid, err_message = await CheckForReason(
        group=group,
        reason=body.created_reason or "",
        members_to_add=body.members_to_add,
        owners_to_add=body.owners_to_add,
    ).execute_for_group()
    if not valid:
        raise HTTPException(400, err_message)

    await ModifyGroupUsers(
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

    return await get_group_members(group_id, db, current_user_id)
