"""Roles router. Roles are RoleGroups; this router exposes endpoints scoped
to that subtype. Most logic is delegated to the groups router via shared
helpers.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import func, nullsfirst, or_, select
from starlette.requests import Request

from api.auth import permissions as _perms
from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import ModifyRoleGroups
from fastapi_pagination.ext.sqlalchemy import apaginate

from sqlalchemy.orm import selectinload

from api.pagination import Page, validated
from api.routers._eager import group_tag_map_options, role_group_map_options
from api.routers._fan_out import defer_fan_out
from api.schemas import (
    RoleGroupListItem,
    GroupDetail,
    RoleMembersSummary,
    SearchRoleQuery,
)
from api.schemas.requests_schemas import RoleMember

router = APIRouter(prefix="/api/roles", tags=["roles"], dependencies=[Depends(defer_fan_out)])
# `GroupDetail` is a discriminated union — see the same comment in
# `api/routers/groups.py`.
_role_adapter: TypeAdapter[Any] = TypeAdapter(GroupDetail)

# `get_role` fetches `select(RoleGroup)` and serializes it as `RoleGroupDetail`,
# which exposes only `active_group_tags` and the role→group *association*
# mappings. Reusing groups' polymorphic `DEFAULT_LOAD_OPTIONS` here also
# selectin-loaded the own-group `active_role_member/owner_mappings` — which are
# structurally empty for a role (a role can't be the target of a RoleGroupMap),
# so two wasted round-trips per fetch — plus AppGroup-only loaders that never
# apply to a role row. Load exactly what the schema emits instead.
_ROLE_LOAD_OPTIONS = (
    selectinload(RoleGroup.active_group_tags).options(*group_tag_map_options()),
    selectinload(RoleGroup.active_role_associated_group_member_mappings).options(*role_group_map_options()),
    selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(*role_group_map_options()),
)


@router.get("", name="roles")
async def list_roles(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchRoleQuery, Query()],
) -> Page[RoleGroupListItem]:
    # Flask `RoleList.get()` `only=(id, type, name, description, created_at,
    # updated_at)` — `RoleGroupListItem` matches that shape exactly. The list
    # shape exposes none of the eager-loaded relationships, so we attach no
    # load options here at all (each would be an extra round trip discarded).
    stmt = select(RoleGroup).where(RoleGroup.deleted_at.is_(None)).order_by(func.lower(RoleGroup.name))

    # Filter to roles owned by `owner_id` (id or email; supports `@me`).
    if q_args.owner_id:
        owner_id = current_user_id if q_args.owner_id == "@me" else q_args.owner_id
        owner = (
            await db.scalars(
                select(OktaUser)
                .where(or_(OktaUser.id == owner_id, OktaUser.email.ilike(owner_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
            )
        ).first()
        if owner is None:
            raise HTTPException(404, "Not Found")
        owned_role_ids = (
            await db.scalars(
                select(OktaUserGroupMember.group_id)
                .where(OktaUserGroupMember.user_id == owner.id)
                .where(OktaUserGroupMember.is_owner.is_(True))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
            )
        ).all()
        stmt = stmt.where(RoleGroup.id.in_(owned_role_ids))

    if q_args.q:
        like = f"%{q_args.q}%"
        stmt = stmt.where(or_(RoleGroup.name.ilike(like), RoleGroup.description.ilike(like)))
    return await apaginate(db, stmt, transformer=validated(RoleGroupListItem))


@router.get("/{role_id}", name="role_by_id")
async def get_role(role_id: str, db: DbSession, current_user_id: CurrentUserId) -> GroupDetail:
    role = (
        await db.scalars(
            select(RoleGroup)
            .options(*_ROLE_LOAD_OPTIONS)
            .where(or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
            .order_by(nullsfirst(RoleGroup.deleted_at.desc()))
        )
    ).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    return _role_adapter.validate_python(role, from_attributes=True)


@router.get("/{role_id}/audit", name="role_audit_by_id")
async def get_role_audit(role_id: str, request: Request, current_user_id: CurrentUserId) -> RedirectResponse:
    from urllib.parse import urlencode

    qp = dict(request.query_params)
    qp["role_id"] = role_id
    return RedirectResponse(url=f"/api/audit/groups?{urlencode(qp)}", status_code=307)


@router.get("/{role_id}/members", name="role_members_by_id")
async def get_role_members(role_id: str, db: DbSession, current_user_id: CurrentUserId) -> RoleMembersSummary:
    role = (
        await db.scalars(
            select(RoleGroup)
            .where(RoleGroup.deleted_at.is_(None))
            .where(or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        )
    ).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    mappings = (
        await db.scalars(
            select(RoleGroupMap)
            .where(RoleGroupMap.role_group_id == role.id)
            .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
        )
    ).all()
    return RoleMembersSummary(
        groups_in_role=[m.group_id for m in mappings if not m.is_owner],
        groups_owned_by_role=[m.group_id for m in mappings if m.is_owner],
    )


@router.put("/{role_id}/members", name="role_members_by_id_put")
async def put_role_members(
    role_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    body: RoleMember | None = None,
) -> RoleMembersSummary:
    from sqlalchemy.orm import with_polymorphic

    from api.models import AppGroup, RoleGroupMap
    from api.operations.constraints import CheckForReason, CheckForSelfAdd

    role = (
        await db.scalars(
            select(RoleGroup)
            .where(RoleGroup.deleted_at.is_(None))
            .where(or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        )
    ).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    # Body is `Optional` so the missing-role 404 above fires even when the
    # client sends no body. After the 404 we still require a body for the
    # actual mutation. Mirrors `groups.py` PUT members.
    if body is None:
        raise HTTPException(400, "Request body is required")

    # Authorization: should_expire requires can_manage_group on each affected group.
    if body.groups_should_expire or body.owner_groups_should_expire:
        all_should_expire_ids = body.groups_should_expire + body.owner_groups_should_expire
        maps = (
            await db.scalars(
                select(RoleGroupMap)
                .where(RoleGroupMap.id.in_(all_should_expire_ids))
                .where(RoleGroupMap.role_group_id == role.id)
            )
        ).all()
        affected_group_ids = [m.group_id for m in maps]
        affected_groups = (
            await db.scalars(
                select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id.in_(affected_group_ids))
            )
        ).all()
        for g in affected_groups:
            if not await _perms.can_manage_group(db, current_user_id, g):
                raise HTTPException(403, "Current user is not allowed to perform this action")

    if not await _perms.is_access_admin(db, current_user_id):
        # Each group being added: must be group owner or app owner.
        added_groups = (
            await db.scalars(
                select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id.in_(body.groups_to_add + body.owner_groups_to_add))
            )
        ).all()
        for g in added_groups:
            if not await _perms.is_group_owner(db, current_user_id, g) and not await _perms.is_app_owner_group_owner(
                db, current_user_id, app_group=g if isinstance(g, AppGroup) else None
            ):
                raise HTTPException(403, "Current user is not allowed to perform this action")

        # Each group being removed: role owners exempt; otherwise must own the group/app.
        if not await _perms.is_group_owner(db, current_user_id, role):
            removed_groups = (
                await db.scalars(
                    select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                    .where(OktaGroup.deleted_at.is_(None))
                    .where(OktaGroup.id.in_(body.groups_to_remove + body.owner_groups_to_remove))
                )
            ).all()
            for g in removed_groups:
                if not await _perms.is_group_owner(
                    db, current_user_id, g
                ) and not await _perms.is_app_owner_group_owner(
                    db, current_user_id, app_group=g if isinstance(g, AppGroup) else None
                ):
                    raise HTTPException(403, "Current user is not allowed to perform this action")

    # Reject changes to unmanaged groups.
    affected_ids = body.groups_to_add + body.owner_groups_to_add + body.groups_to_remove + body.owner_groups_to_remove
    if affected_ids:
        unmanaged_count = await db.scalar(
            select(func.count(OktaGroup.id))
            .where(OktaGroup.id.in_(affected_ids))
            .where(OktaGroup.deleted_at.is_(None))
            .where(OktaGroup.is_managed.is_(False))
        )
        if unmanaged_count and unmanaged_count > 0:
            raise HTTPException(400, "Groups not managed by Access cannot be modified")

    # Reject role-in-role nesting. Roles can only contain non-role groups.
    add_ids = body.groups_to_add + body.owner_groups_to_add
    if add_ids:
        role_in_add_count = await db.scalar(
            select(func.count(OktaGroup.id))
            .where(OktaGroup.id.in_(add_ids))
            .where(OktaGroup.deleted_at.is_(None))
            .where(OktaGroup.type == RoleGroup.__mapper_args__["polymorphic_identity"])
        )
        if role_in_add_count and role_in_add_count > 0:
            raise HTTPException(400, "Roles cannot be added to other Roles")

    valid, err_message = await CheckForSelfAdd(
        group=role,
        current_user=current_user_id,
        members_to_add=body.groups_to_add,
        owners_to_add=body.owner_groups_to_add,
    ).execute_for_role()
    if not valid:
        raise HTTPException(400, err_message)

    valid, err_message = await CheckForReason(
        group=role,
        reason=body.created_reason or "",
        members_to_add=body.groups_to_add,
        owners_to_add=body.owner_groups_to_add,
    ).execute_for_role()
    if not valid:
        raise HTTPException(400, err_message)

    await ModifyRoleGroups(
        role_group=role,
        groups_to_add=body.groups_to_add,
        groups_to_remove=body.groups_to_remove,
        owner_groups_to_add=body.owner_groups_to_add,
        owner_groups_to_remove=body.owner_groups_to_remove,
        groups_should_expire=body.groups_should_expire,
        owner_groups_should_expire=body.owner_groups_should_expire,
        groups_added_ended_at=body.groups_added_ending_at,
        current_user_id=current_user_id,
        created_reason=body.created_reason or "",
    ).execute()
    return await get_role_members(role_id, db, current_user_id)
