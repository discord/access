"""Audit router. Provides:

  GET /api/audit/users    — user/group membership audit (OktaUserGroupMember rows)
  GET /api/audit/groups   — group/role mapping audit (RoleGroupMap rows)

Both endpoints return rows with nested related objects (user, group,
role_group, created_actor, ended_actor, role_group_mapping). The response
shape is the one the React frontend expects to render the Expiring access /
Expiring roles pages and the per-user / per-group audit views.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, nullsfirst, nullslast
from sqlalchemy.orm import aliased, joinedload, selectin_polymorphic, selectinload, with_polymorphic
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import (
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.pagination import paginate
from api.schemas import (
    AuditOrderBy,
    SearchGroupRoleAuditPaginationQuery,
    SearchUserGroupAuditPaginationQuery,
)
from api.schemas.rfc822 import _rfc822

router = APIRouter(prefix="/api/audit", tags=["audit"])


# --- Resolution helpers -----------------------------------------------------


def _unix_to_utc_naive(ts: int) -> datetime:
    """Unix timestamp → naive UTC datetime, matching how `ended_at` is stored.

    Bare `datetime.fromtimestamp(ts)` returns naive *local* time, so comparing
    it against a naive-UTC column drifts by the server's UTC offset.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _resolve_me(value: str | None, current_user_id: str) -> str | None:
    return current_user_id if value == "@me" else value


def _resolve_user(db: DbSession, value: str | None) -> OktaUser | None:
    if value is None:
        return None
    user = (
        db.query(OktaUser)
        .filter(_db.or_(OktaUser.id == value, OktaUser.email.ilike(value)))
        .order_by(nullsfirst(OktaUser.deleted_at.desc()))
        .first()
    )
    if user is None:
        raise HTTPException(404, "Not Found")
    return user


def _resolve_group(db: DbSession, value: str | None) -> OktaGroup | None:
    if value is None:
        return None
    group = (
        db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
        .filter(_db.or_(OktaGroup.id == value, OktaGroup.name == value))
        .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
        .first()
    )
    if group is None:
        raise HTTPException(404, "Not Found")
    return group


def _resolve_role(db: DbSession, value: str | None) -> RoleGroup | None:
    if value is None:
        return None
    role = (
        db.query(RoleGroup)
        .filter(_db.or_(RoleGroup.id == value, RoleGroup.name == value))
        .order_by(nullsfirst(RoleGroup.deleted_at.desc()))
        .first()
    )
    if role is None:
        raise HTTPException(404, "Not Found")
    return role


# --- Serializers ------------------------------------------------------------


def _user_summary(u: Any) -> dict[str, Any] | None:
    if u is None:
        return None
    return {
        "id": u.id,
        "email": u.email,
        "first_name": getattr(u, "first_name", None),
        "last_name": getattr(u, "last_name", None),
        "display_name": getattr(u, "display_name", None),
        "deleted_at": _rfc822(u.deleted_at) if getattr(u, "deleted_at", None) else None,
        "created_at": _rfc822(u.created_at) if getattr(u, "created_at", None) else None,
    }


def _app_ref(a: Any) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "id": a.id,
        "name": getattr(a, "name", None),
        "deleted_at": _rfc822(a.deleted_at) if getattr(a, "deleted_at", None) else None,
    }


def _group_ref(g: Any) -> dict[str, Any] | None:
    if g is None:
        return None
    out: dict[str, Any] = {
        "id": g.id,
        "type": g.type,
        "name": g.name,
        "is_owner": getattr(g, "is_owner", None),
        "is_managed": getattr(g, "is_managed", None),
        "deleted_at": _rfc822(g.deleted_at) if getattr(g, "deleted_at", None) else None,
    }
    if isinstance(g, AppGroup):
        out["app"] = _app_ref(getattr(g, "app", None))
    return out


def _role_group_ref(r: Any) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "id": r.id,
        "type": r.type,
        "name": r.name,
        "is_managed": getattr(r, "is_managed", None),
        "deleted_at": _rfc822(r.deleted_at) if getattr(r, "deleted_at", None) else None,
    }


def _role_group_mapping_ref(rgm: Any) -> dict[str, Any] | None:
    if rgm is None:
        return None
    return {
        "created_at": _rfc822(rgm.created_at) if rgm.created_at else None,
        "ended_at": _rfc822(rgm.ended_at) if rgm.ended_at else None,
        "role_group": _role_group_ref(getattr(rgm, "role_group", None)),
    }


def _access_request_ref(ar: Any) -> dict[str, Any] | None:
    if ar is None:
        return None
    return {"id": ar.id}


def _serialize_user_group_member(m: OktaUserGroupMember) -> dict[str, Any]:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "group_id": m.group_id,
        "role_group_map_id": getattr(m, "role_group_map_id", None),
        "is_owner": m.is_owner,
        "should_expire": getattr(m, "should_expire", None),
        "created_reason": m.created_reason or "",
        "created_at": _rfc822(m.created_at) if m.created_at else None,
        "updated_at": _rfc822(m.updated_at) if getattr(m, "updated_at", None) else None,
        "ended_at": _rfc822(m.ended_at) if m.ended_at else None,
        "user": _user_summary(getattr(m, "user", None)),
        "active_user": _user_summary(getattr(m, "active_user", None)),
        "group": _group_ref(getattr(m, "group", None)),
        "active_group": _group_ref(getattr(m, "active_group", None)),
        "role_group_mapping": _role_group_mapping_ref(getattr(m, "role_group_mapping", None)),
        "active_role_group_mapping": _role_group_mapping_ref(getattr(m, "active_role_group_mapping", None)),
        "access_request": _access_request_ref(getattr(m, "access_request", None)),
        "created_actor": _user_summary(getattr(m, "created_actor", None)),
        "ended_actor": _user_summary(getattr(m, "ended_actor", None)),
    }


def _serialize_role_group_map(rgm: RoleGroupMap) -> dict[str, Any]:
    return {
        "id": rgm.id,
        "role_group_id": rgm.role_group_id,
        "group_id": rgm.group_id,
        "is_owner": rgm.is_owner,
        "should_expire": getattr(rgm, "should_expire", None),
        "created_reason": getattr(rgm, "created_reason", "") or "",
        "created_at": _rfc822(rgm.created_at) if rgm.created_at else None,
        "ended_at": _rfc822(rgm.ended_at) if rgm.ended_at else None,
        "group": _group_ref(getattr(rgm, "group", None)),
        "active_group": _group_ref(getattr(rgm, "active_group", None)),
        "role_group": _role_group_ref(getattr(rgm, "role_group", None)),
        "active_role_group": _role_group_ref(getattr(rgm, "active_role_group", None)),
        "created_actor": _user_summary(getattr(rgm, "created_actor", None)),
        "ended_actor": _user_summary(getattr(rgm, "ended_actor", None)),
    }


# --- Routes -----------------------------------------------------------------


@router.get("/users", name="users_and_groups")
def users_and_groups(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchUserGroupAuditPaginationQuery, Query()],
) -> dict[str, Any]:
    user_id = _resolve_me(q_args.user_id, current_user_id)
    owner_id = _resolve_me(q_args.owner_id, current_user_id)
    user = _resolve_user(db, user_id)
    group = _resolve_group(db, q_args.group_id)
    owner = _resolve_user(db, owner_id)

    group_alias = aliased(OktaGroup)

    query = (
        db.query(OktaUserGroupMember)
        .options(
            joinedload(OktaUserGroupMember.user),
            joinedload(OktaUserGroupMember.active_user),
            joinedload(OktaUserGroupMember.created_actor),
            joinedload(OktaUserGroupMember.ended_actor),
            joinedload(OktaUserGroupMember.access_request),
            selectinload(OktaUserGroupMember.group).options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
                selectinload(OktaGroup.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_tag),
                    joinedload(OktaGroupTagMap.active_app_tag_mapping),
                ),
            ),
            selectinload(OktaUserGroupMember.active_group).options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
            ),
            selectinload(OktaUserGroupMember.role_group_mapping).joinedload(RoleGroupMap.role_group),
            selectinload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
        )
        .join(OktaUserGroupMember.user)
        .join(OktaUserGroupMember.group.of_type(group_alias))
    )

    if user is not None:
        query = query.filter(OktaUserGroupMember.user_id == user.id)
    if group is not None:
        query = query.filter(OktaUserGroupMember.group_id == group.id)

    # Owner filter — only return memberships in groups owned by `owner`,
    # either directly or transitively via the owning app.
    if owner is not None:
        owner_group_ids = [
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
        app_owner_group_ids = [
            ag.id
            for ag in db.query(AppGroup)
            .filter(AppGroup.id.in_(owner_group_ids))
            .filter(AppGroup.is_owner.is_(True))
            .all()
        ]
        app_ids = [ag.app_id for ag in db.query(AppGroup).filter(AppGroup.id.in_(app_owner_group_ids)).all()]
        app_groups_owned_ids = [
            ag.id
            for ag in db.query(AppGroup)
            .filter(AppGroup.app_id.in_(app_ids))
            .filter(AppGroup.deleted_at.is_(None))
            .all()
        ]
        if q_args.app_owner is True:
            query = query.filter(
                _db.or_(
                    OktaUserGroupMember.group_id.in_(owner_group_ids),
                    OktaUserGroupMember.group_id.in_(app_groups_owned_ids),
                )
            )
        else:
            # Exclude app groups that have at least one direct owner who isn't `owner`.
            directly_owned_by_others_ids = [
                m.group_id
                for m in db.query(OktaUserGroupMember.group_id)
                .filter(OktaUserGroupMember.group_id.in_(app_groups_owned_ids))
                .filter(OktaUserGroupMember.user_id != owner.id)
                .filter(OktaUserGroupMember.is_owner.is_(True))
                .filter(
                    _db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > _db.func.now(),
                    )
                )
                .all()
            ]
            visible_app_groups = set(app_groups_owned_ids) - set(directly_owned_by_others_ids)
            query = query.filter(
                _db.or_(
                    OktaUserGroupMember.group_id.in_(owner_group_ids),
                    OktaUserGroupMember.group_id.in_(visible_app_groups),
                )
            )

    if q_args.owner is not None:
        query = query.filter(OktaUserGroupMember.is_owner == q_args.owner)

    if q_args.active is True:
        query = query.filter(
            _db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > _db.func.now(),
            )
        )
    elif q_args.active is False:
        query = query.filter(
            _db.and_(
                OktaUserGroupMember.ended_at.is_not(None),
                OktaUserGroupMember.ended_at < _db.func.now(),
            )
        )

    if q_args.needs_review is True:
        query = query.filter(OktaUserGroupMember.should_expire.is_(False))

    if q_args.direct is True:
        query = query.filter(_db.not_(OktaUserGroupMember.active_role_group_mapping.has()))

    if q_args.deleted is False:
        query = query.filter(OktaUser.deleted_at.is_(None))

    if q_args.managed is not None:
        query = query.filter(group_alias.is_managed == q_args.managed)

    if q_args.start_date is not None and q_args.end_date is not None:
        query = query.filter(
            _db.and_(
                OktaUserGroupMember.ended_at.is_not(None),
                OktaUserGroupMember.ended_at > _unix_to_utc_naive(q_args.start_date),
                OktaUserGroupMember.ended_at < _unix_to_utc_naive(q_args.end_date),
            )
        )

    if q_args.q:
        like = f"%{q_args.q}%"
        query = query.filter(
            _db.or_(
                group_alias.name.ilike(like),
                OktaUser.email.ilike(like),
                OktaUser.first_name.ilike(like),
                OktaUser.last_name.ilike(like),
                OktaUser.display_name.ilike(like),
            )
        )

    nulls_order = nullsfirst if q_args.order_desc else nullslast
    if q_args.order_by == AuditOrderBy.moniker:
        if user is not None:
            ordering = (
                nulls_order(getattr(group_alias.name, "desc" if q_args.order_desc else "asc")()),
                nullslast(OktaUserGroupMember.created_at.asc()),
            )
        else:
            ordering = (
                nulls_order(getattr(func.lower(OktaUser.email), "desc" if q_args.order_desc else "asc")()),
                nullslast(OktaUserGroupMember.created_at.asc()),
            )
    else:
        col = getattr(OktaUserGroupMember, q_args.order_by.value)
        ordering = (nulls_order(getattr(col, "desc" if q_args.order_desc else "asc")()),)
    query = query.order_by(*ordering)

    return paginate(
        request,
        query,
        _serialize_user_group_member,
        extract=lambda: (q_args.page, q_args.per_page),
    )


@router.get("/groups", name="groups_and_roles")
def groups_and_roles(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchGroupRoleAuditPaginationQuery, Query()],
) -> dict[str, Any]:
    from api.auth.permissions import is_access_admin

    role_id = _resolve_me(q_args.role_id, current_user_id)
    owner_id = _resolve_me(q_args.owner_id, current_user_id)
    role_owner_id = _resolve_me(q_args.role_owner_id, current_user_id)
    role = _resolve_role(db, role_id)
    group = _resolve_group(db, q_args.group_id)
    owner = _resolve_user(db, owner_id)
    role_owner = _resolve_user(db, role_owner_id)

    group_alias = aliased(OktaGroup)

    query = (
        db.query(RoleGroupMap)
        .options(
            joinedload(RoleGroupMap.role_group),
            joinedload(RoleGroupMap.active_role_group),
            joinedload(RoleGroupMap.created_actor),
            joinedload(RoleGroupMap.ended_actor),
            selectinload(RoleGroupMap.group).options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
                selectinload(OktaGroup.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_tag),
                    joinedload(OktaGroupTagMap.active_app_tag_mapping),
                ),
            ),
            selectinload(RoleGroupMap.active_group).options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
            ),
        )
        .join(RoleGroupMap.role_group)
        .join(RoleGroupMap.group.of_type(group_alias))
    )

    if role is not None:
        query = query.filter(RoleGroupMap.role_group_id == role.id)
    if group is not None:
        query = query.filter(RoleGroupMap.group_id == group.id)

    if owner is not None:
        owner_group_ids = [
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
        app_owner_group_ids = [
            ag.id
            for ag in db.query(AppGroup)
            .filter(AppGroup.id.in_(owner_group_ids))
            .filter(AppGroup.is_owner.is_(True))
            .all()
        ]
        app_ids = [ag.app_id for ag in db.query(AppGroup).filter(AppGroup.id.in_(app_owner_group_ids)).all()]
        app_groups_owned_ids = [
            ag.id
            for ag in db.query(AppGroup)
            .filter(AppGroup.app_id.in_(app_ids))
            .filter(AppGroup.deleted_at.is_(None))
            .all()
        ]
        if q_args.app_owner is True:
            query = query.filter(
                _db.or_(
                    RoleGroupMap.group_id.in_(owner_group_ids),
                    RoleGroupMap.group_id.in_(app_groups_owned_ids),
                )
            )
        else:
            directly_owned_by_others_ids = [
                m.group_id
                for m in db.query(OktaUserGroupMember.group_id)
                .filter(OktaUserGroupMember.group_id.in_(app_groups_owned_ids))
                .filter(OktaUserGroupMember.user_id != owner.id)
                .filter(OktaUserGroupMember.is_owner.is_(True))
                .filter(
                    _db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > _db.func.now(),
                    )
                )
                .all()
            ]
            visible_app_groups = set(app_groups_owned_ids) - set(directly_owned_by_others_ids)
            query = query.filter(
                _db.or_(
                    RoleGroupMap.group_id.in_(owner_group_ids),
                    RoleGroupMap.group_id.in_(visible_app_groups),
                )
            )

    if role_owner is not None:
        role_owner_role_ids = [
            row.group_id
            for row in db.query(OktaUserGroupMember.group_id)
            .filter(OktaUserGroupMember.user_id == role_owner.id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                _db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > _db.func.now(),
                )
            )
            .all()
        ]
        # Access admins additionally see roles that have NO active owner —
        # those are the roles only an admin can resolve expiring access for.
        unowned_admin_role_ids: list[str] = []
        if is_access_admin(db, role_owner.id):
            owners_subquery = (
                db.query(OktaUserGroupMember.group_id)
                .filter(
                    _db.and_(
                        OktaUserGroupMember.is_owner.is_(True),
                        _db.or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > _db.func.now(),
                        ),
                    )
                )
                .subquery()
            )
            unowned_admin_role_ids = [
                rg.id
                for rg in db.query(RoleGroup)
                .filter(_db.and_(RoleGroup.deleted_at.is_(None), ~RoleGroup.id.in_(owners_subquery)))
                .all()
            ]
        query = query.filter(
            _db.or_(
                RoleGroupMap.role_group_id.in_(role_owner_role_ids),
                RoleGroupMap.role_group_id.in_(unowned_admin_role_ids),
            )
        )

    if q_args.owner is not None:
        query = query.filter(RoleGroupMap.is_owner == q_args.owner)

    if q_args.active is True:
        query = query.filter(
            _db.or_(
                RoleGroupMap.ended_at.is_(None),
                RoleGroupMap.ended_at > _db.func.now(),
            )
        )
    elif q_args.active is False:
        query = query.filter(
            _db.and_(
                RoleGroupMap.ended_at.is_not(None),
                RoleGroupMap.ended_at < _db.func.now(),
            )
        )

    if q_args.needs_review is True:
        query = query.filter(RoleGroupMap.should_expire.is_(False))

    if q_args.managed is not None:
        query = query.filter(group_alias.is_managed == q_args.managed)

    if q_args.start_date is not None and q_args.end_date is not None:
        query = query.filter(
            _db.and_(
                RoleGroupMap.ended_at.is_not(None),
                RoleGroupMap.ended_at > _unix_to_utc_naive(q_args.start_date),
                RoleGroupMap.ended_at < _unix_to_utc_naive(q_args.end_date),
            )
        )

    if q_args.q:
        like = f"%{q_args.q}%"
        query = query.filter(
            _db.or_(
                RoleGroup.id.ilike(like),
                RoleGroup.name.ilike(like),
                RoleGroup.description.ilike(like),
                group_alias.id.ilike(like),
                group_alias.name.ilike(like),
                group_alias.description.ilike(like),
            )
        )

    nulls_order = nullsfirst if q_args.order_desc else nullslast
    if q_args.order_by == AuditOrderBy.moniker:
        target = RoleGroup.name if role is None else group_alias.name
        ordering = (
            nulls_order(getattr(target, "desc" if q_args.order_desc else "asc")()),
            nullslast(RoleGroupMap.created_at.asc()),
        )
    else:
        col = getattr(RoleGroupMap, q_args.order_by.value)
        ordering = (nulls_order(getattr(col, "desc" if q_args.order_desc else "asc")()),)
    query = query.order_by(*ordering)

    return paginate(
        request,
        query,
        _serialize_role_group_map,
        extract=lambda: (q_args.page, q_args.per_page),
    )
