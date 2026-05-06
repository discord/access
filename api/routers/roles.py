"""Roles router. Roles are RoleGroups; this router exposes endpoints scoped
to that subtype. Most logic is delegated to the groups router via shared
helpers.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import func, nullsfirst
from starlette.requests import Request

from api.auth import permissions as _perms
from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import ModifyRoleGroups
from api.pagination import paginate
from api.routers.groups import DEFAULT_LOAD_OPTIONS as _GROUP_LOAD_OPTIONS
from api.schemas import GroupDetail, GroupSummary, SearchRolePaginationQuery
from api.schemas._serialize import dump_orm
from api.schemas.requests_schemas import RoleMember

router = APIRouter(prefix="/api/roles", tags=["roles"])
_role_adapter = TypeAdapter(GroupDetail)
_role_summary_adapter = TypeAdapter(GroupSummary)


@router.get("", name="roles")
def list_roles(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchRolePaginationQuery, Query()],
) -> dict[str, Any]:
    query = (
        db.query(RoleGroup)
        .options(*_GROUP_LOAD_OPTIONS)
        .filter(RoleGroup.deleted_at.is_(None))
        .order_by(func.lower(RoleGroup.name))
    )

    # Filter to roles owned by `owner_id` (id or email; supports `@me`).
    if q_args.owner_id:
        owner_id = current_user_id if q_args.owner_id == "@me" else q_args.owner_id
        owner = (
            db.query(OktaUser)
            .filter(_db.or_(OktaUser.id == owner_id, OktaUser.email.ilike(owner_id)))
            .order_by(nullsfirst(OktaUser.deleted_at.desc()))
            .first()
        )
        if owner is None:
            raise HTTPException(404, "Not Found")
        owned_role_ids = [
            row.group_id
            for row in db.query(OktaUserGroupMember.group_id)
            .filter(OktaUserGroupMember.user_id == owner.id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                _db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > _db.func.now(),
                )
            )
            .all()
        ]
        query = query.filter(RoleGroup.id.in_(owned_role_ids))

    if q_args.q:
        like = f"%{q_args.q}%"
        query = query.filter(_db.or_(RoleGroup.name.ilike(like), RoleGroup.description.ilike(like)))
    return paginate(request, query, _role_summary_adapter, extract=lambda: (q_args.page, q_args.per_page))


@router.get("/{role_id}", name="role_by_id")
def get_role(role_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    role = (
        db.query(RoleGroup)
        .options(*_GROUP_LOAD_OPTIONS)
        .filter(_db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        .first()
    )
    if role is None:
        raise HTTPException(404, "Not Found")
    return dump_orm(_role_adapter, role)


@router.get("/{role_id}/audit", name="role_audit_by_id")
def get_role_audit(role_id: str, request: Request, current_user_id: CurrentUserId) -> RedirectResponse:
    from urllib.parse import urlencode

    qp = dict(request.query_params)
    qp["role_id"] = role_id
    return RedirectResponse(url=f"/api/audit/groups?{urlencode(qp)}", status_code=307)


@router.get("/{role_id}/members", name="role_members_by_id")
def get_role_members(role_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    role = db.query(RoleGroup).filter(_db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id)).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    mappings = (
        db.query(RoleGroupMap)
        .filter(RoleGroupMap.role_group_id == role.id)
        .filter(_db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > _db.func.now()))
        .all()
    )
    return {
        "groups_in_role": [m.group_id for m in mappings if not m.is_owner],
        "groups_owned_by_role": [m.group_id for m in mappings if m.is_owner],
    }


@router.put("/{role_id}/members", name="role_members_by_id_put")
def put_role_members(
    role_id: str,
    db: DbSession,
    current_user_id: CurrentUserId,
    body: RoleMember | None = None,
) -> dict[str, Any]:
    from sqlalchemy.orm import with_polymorphic

    from api.models import AppGroup, RoleGroupMap
    from api.operations.constraints import CheckForReason, CheckForSelfAdd

    if body is None:
        body = RoleMember()
    role = (
        db.query(RoleGroup)
        .filter(RoleGroup.deleted_at.is_(None))
        .filter(_db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
        .first()
    )
    if role is None:
        raise HTTPException(404, "Not Found")

    # Authorization: should_expire requires can_manage_group on each affected group.
    if body.groups_should_expire or body.owner_groups_should_expire:
        all_should_expire_ids = body.groups_should_expire + body.owner_groups_should_expire
        maps = (
            db.query(RoleGroupMap)
            .filter(RoleGroupMap.id.in_(all_should_expire_ids))
            .filter(RoleGroupMap.role_group_id == role.id)
            .all()
        )
        affected_group_ids = [m.group_id for m in maps]
        affected_groups = (
            db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id.in_(affected_group_ids))
            .all()
        )
        for g in affected_groups:
            if not _perms.can_manage_group(db, current_user_id, g):
                raise HTTPException(403, "Current user is not allowed to perform this action")

    if not _perms.is_access_admin(db, current_user_id):
        # Each group being added: must be group owner or app owner.
        added_groups = (
            db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id.in_(body.groups_to_add + body.owner_groups_to_add))
            .all()
        )
        for g in added_groups:
            if not _perms.is_group_owner(db, current_user_id, g) and not _perms.is_app_owner_group_owner(
                db, current_user_id, app_group=g if isinstance(g, AppGroup) else None
            ):
                raise HTTPException(403, "Current user is not allowed to perform this action")

        # Each group being removed: role owners exempt; otherwise must own the group/app.
        if not _perms.is_group_owner(db, current_user_id, role):
            removed_groups = (
                db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(OktaGroup.deleted_at.is_(None))
                .filter(OktaGroup.id.in_(body.groups_to_remove + body.owner_groups_to_remove))
                .all()
            )
            for g in removed_groups:
                if not _perms.is_group_owner(db, current_user_id, g) and not _perms.is_app_owner_group_owner(
                    db, current_user_id, app_group=g if isinstance(g, AppGroup) else None
                ):
                    raise HTTPException(403, "Current user is not allowed to perform this action")

    # Reject changes to unmanaged groups.
    affected_ids = body.groups_to_add + body.owner_groups_to_add + body.groups_to_remove + body.owner_groups_to_remove
    if affected_ids:
        unmanaged_count = (
            db.query(_db.func.count(OktaGroup.id))
            .filter(OktaGroup.id.in_(affected_ids))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.is_managed.is_(False))
            .scalar()
        )
        if unmanaged_count and unmanaged_count > 0:
            raise HTTPException(400, "Groups not managed by Access cannot be modified")

    # Reject role-in-role nesting. Roles can only contain non-role groups.
    add_ids = body.groups_to_add + body.owner_groups_to_add
    if add_ids:
        role_in_add_count = (
            db.query(_db.func.count(OktaGroup.id))
            .filter(OktaGroup.id.in_(add_ids))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.type == RoleGroup.__mapper_args__["polymorphic_identity"])
            .scalar()
        )
        if role_in_add_count and role_in_add_count > 0:
            raise HTTPException(400, "Roles cannot be added to other Roles")

    valid, err_message = CheckForSelfAdd(
        group=role,
        current_user=current_user_id,
        members_to_add=body.groups_to_add,
        owners_to_add=body.owner_groups_to_add,
    ).execute_for_role()
    if not valid:
        raise HTTPException(400, err_message)

    valid, err_message = CheckForReason(
        group=role,
        reason=body.created_reason or "",
        members_to_add=body.groups_to_add,
        owners_to_add=body.owner_groups_to_add,
    ).execute_for_role()
    if not valid:
        raise HTTPException(400, err_message)

    ModifyRoleGroups(
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
    return get_role_members(role_id, db, current_user_id)
