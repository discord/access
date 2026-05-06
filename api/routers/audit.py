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
    GroupRoleAuditPagination,
    SearchGroupRoleAuditPaginationQuery,
    SearchUserGroupAuditPaginationQuery,
    UserGroupAuditPagination,
)
from api.schemas.audit_rows import (
    AuditGroupRoleRow,
    AuditUserGroupRow,
    _AccessRequestRef,
    _AppRefForAudit,
    _GroupRefForAudit,
    _RoleAssociatedMappingForAudit,
    _RoleGroupMappingForAudit,
    _RoleGroupRefForAudit,
    _UserSummaryForAudit,
)

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
# Each `_*_for_audit` helper builds a Pydantic instance from an ORM row.
# `model_validate(..., from_attributes=True)` is sufficient for the simple
# refs (their fields line up with ORM attribute names); the polymorphic
# group ref is constructed by hand to thread the conditional
# `active_role_associated_group_*_mappings` keys.


def _user_summary_for_audit(u: Any) -> _UserSummaryForAudit | None:
    if u is None:
        return None
    return _UserSummaryForAudit.model_validate(u, from_attributes=True)


def _app_ref_for_audit(a: Any) -> _AppRefForAudit | None:
    if a is None:
        return None
    return _AppRefForAudit.model_validate(a, from_attributes=True)


def _role_group_ref_for_audit(r: Any) -> _RoleGroupRefForAudit | None:
    if r is None:
        return None
    return _RoleGroupRefForAudit.model_validate(r, from_attributes=True)


def _role_group_mapping_for_audit(rgm: Any) -> _RoleGroupMappingForAudit | None:
    if rgm is None:
        return None
    return _RoleGroupMappingForAudit(
        created_at=rgm.created_at,
        ended_at=rgm.ended_at,
        role_group=_role_group_ref_for_audit(getattr(rgm, "role_group", None)),
    )


def _role_associated_mapping_for_audit(rgm: Any) -> _RoleAssociatedMappingForAudit | None:
    """Mapping ref used inside `group.active_role_associated_group_*_mappings`
    on the user-group audit endpoint. Surfaces the active group on the other
    side of the role association so the React UI can render the "this role
    pulls in these groups" rollup."""
    if rgm is None:
        return None
    return _RoleAssociatedMappingForAudit(
        id=rgm.id,
        is_owner=rgm.is_owner,
        created_at=rgm.created_at,
        ended_at=rgm.ended_at,
        active_group=_group_ref_for_audit(getattr(rgm, "active_group", None), include_role_associations=False),
    )


def _group_ref_for_audit(g: Any, include_role_associations: bool) -> _GroupRefForAudit | None:
    """Build the audit-row group ref. `include_role_associations` is the
    `user_id is None and group_id is None` condition from the user-group
    audit endpoint — when true and `g` is a `RoleGroup`, this populates the
    `active_role_associated_group_*_mappings` lists. When false the
    `_GroupRefForAudit.@model_serializer` drops those keys from the wire
    output entirely (matching the legacy absent-vs-null behavior)."""
    if g is None:
        return None
    member_mappings: list[_RoleAssociatedMappingForAudit] | None = None
    owner_mappings: list[_RoleAssociatedMappingForAudit] | None = None
    if include_role_associations and isinstance(g, RoleGroup):
        m_raw = getattr(g, "active_role_associated_group_member_mappings", None) or []
        o_raw = getattr(g, "active_role_associated_group_owner_mappings", None) or []
        member_mappings = [r for r in (_role_associated_mapping_for_audit(rgm) for rgm in m_raw) if r is not None]
        owner_mappings = [r for r in (_role_associated_mapping_for_audit(rgm) for rgm in o_raw) if r is not None]
    return _GroupRefForAudit(
        id=g.id,
        type=g.type,
        name=g.name,
        is_owner=getattr(g, "is_owner", None),
        is_managed=getattr(g, "is_managed", None),
        deleted_at=g.deleted_at,
        app=_app_ref_for_audit(getattr(g, "app", None)) if isinstance(g, AppGroup) else None,
        active_role_associated_group_member_mappings=member_mappings,
        active_role_associated_group_owner_mappings=owner_mappings,
    )


def _access_request_ref_for_audit(ar: Any) -> _AccessRequestRef | None:
    if ar is None:
        return None
    return _AccessRequestRef.model_validate(ar, from_attributes=True)


def _audit_user_group_row(m: OktaUserGroupMember, include_role_associations: bool) -> AuditUserGroupRow:
    return AuditUserGroupRow(
        id=m.id,
        user_id=m.user_id,
        group_id=m.group_id,
        role_group_map_id=getattr(m, "role_group_map_id", None),
        is_owner=m.is_owner,
        should_expire=getattr(m, "should_expire", None),
        created_reason=m.created_reason or "",
        created_at=m.created_at,
        updated_at=getattr(m, "updated_at", None),
        ended_at=m.ended_at,
        user=_user_summary_for_audit(getattr(m, "user", None)),
        active_user=_user_summary_for_audit(getattr(m, "active_user", None)),
        group=_group_ref_for_audit(getattr(m, "group", None), include_role_associations),
        active_group=_group_ref_for_audit(getattr(m, "active_group", None), include_role_associations=False),
        role_group_mapping=_role_group_mapping_for_audit(getattr(m, "role_group_mapping", None)),
        active_role_group_mapping=_role_group_mapping_for_audit(getattr(m, "active_role_group_mapping", None)),
        access_request=_access_request_ref_for_audit(getattr(m, "access_request", None)),
        created_actor=_user_summary_for_audit(getattr(m, "created_actor", None)),
        ended_actor=_user_summary_for_audit(getattr(m, "ended_actor", None)),
    )


def _audit_group_role_row(rgm: RoleGroupMap) -> AuditGroupRoleRow:
    return AuditGroupRoleRow(
        id=rgm.id,
        role_group_id=rgm.role_group_id,
        group_id=rgm.group_id,
        is_owner=rgm.is_owner,
        should_expire=getattr(rgm, "should_expire", None),
        created_reason=getattr(rgm, "created_reason", "") or "",
        created_at=rgm.created_at,
        ended_at=rgm.ended_at,
        group=_group_ref_for_audit(getattr(rgm, "group", None), include_role_associations=False),
        active_group=_group_ref_for_audit(getattr(rgm, "active_group", None), include_role_associations=False),
        role_group=_role_group_ref_for_audit(getattr(rgm, "role_group", None)),
        active_role_group=_role_group_ref_for_audit(getattr(rgm, "active_role_group", None)),
        created_actor=_user_summary_for_audit(getattr(rgm, "created_actor", None)),
        ended_actor=_user_summary_for_audit(getattr(rgm, "ended_actor", None)),
    )


# --- Routes -----------------------------------------------------------------


@router.get("/users", name="users_and_groups")
def users_and_groups(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchUserGroupAuditPaginationQuery, Query()],
) -> UserGroupAuditPagination:
    user_id = _resolve_me(q_args.user_id, current_user_id)
    owner_id = _resolve_me(q_args.owner_id, current_user_id)
    user = _resolve_user(db, user_id)
    group = _resolve_group(db, q_args.group_id)
    owner = _resolve_user(db, owner_id)

    group_alias = aliased(OktaGroup)

    # `group.active_role_associated_group_*_mappings` is only surfaced on
    # the response when neither `user_id` nor `group_id` is set; eager-load
    # the relationships only in that case.
    include_role_associations = user is None and group is None

    group_load = selectinload(OktaUserGroupMember.group).options(
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AppGroup.app),
        selectinload(OktaGroup.active_group_tags).options(
            joinedload(OktaGroupTagMap.active_tag),
            joinedload(OktaGroupTagMap.active_app_tag_mapping),
        ),
    )
    if include_role_associations:
        group_load = group_load.options(
            selectinload(RoleGroup.active_role_associated_group_member_mappings).options(
                selectinload(RoleGroupMap.active_group).options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    joinedload(AppGroup.app),
                ),
            ),
            selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(
                selectinload(RoleGroupMap.active_group).options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    joinedload(AppGroup.app),
                ),
            ),
        )

    query = (
        db.query(OktaUserGroupMember)
        .options(
            joinedload(OktaUserGroupMember.user),
            joinedload(OktaUserGroupMember.active_user),
            joinedload(OktaUserGroupMember.created_actor),
            joinedload(OktaUserGroupMember.ended_actor),
            joinedload(OktaUserGroupMember.access_request),
            group_load,
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
        # When filtering by `owner_id`, exclude the owner's own memberships:
        # the frontend's "expiring access" review page expects to see *other*
        # users whose access the owner needs to renew, not the owner.
        query = query.filter(OktaUserGroupMember.user_id != owner.id)

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

    # Free-text `q` — narrow the column set based on which other filters
    # are active so `?user_id=...&q=Alice` searches only group columns
    # (the user is already pinned), not user columns. Symmetric for the
    # group-pinned branch.
    if q_args.q:
        like = f"%{q_args.q}%"
        group_cols = (
            group_alias.id.ilike(like),
            group_alias.name.ilike(like),
            group_alias.description.ilike(like),
        )
        user_cols = (
            OktaUser.id.ilike(like),
            OktaUser.email.ilike(like),
            OktaUser.first_name.ilike(like),
            OktaUser.last_name.ilike(like),
            OktaUser.display_name.ilike(like),
            (OktaUser.first_name + " " + OktaUser.last_name).ilike(like),
        )
        if user is not None:
            query = query.filter(_db.or_(*group_cols))
        if group is not None:
            query = query.filter(_db.or_(*user_cols))
        if owner is not None or (user is None and group is None):
            query = query.filter(_db.or_(*group_cols, *user_cols))

    # Compound order_by — the tail tie-breaker keeps page boundaries stable
    # when two rows share the primary sort value. Without it, paginated
    # results can repeat or skip rows between requests.
    nulls_order = nullsfirst if q_args.order_desc else nullslast

    def _users_audit_ordering() -> tuple:
        if q_args.order_by == AuditOrderBy.moniker:
            primary = group_alias.name if user is not None else func.lower(OktaUser.email)
            primary_dir = primary.desc() if q_args.order_desc else primary.asc()
            return (nulls_order(primary_dir), nullslast(OktaUserGroupMember.created_at.asc()))
        col = getattr(OktaUserGroupMember, q_args.order_by.value)
        primary_dir = col.desc() if q_args.order_desc else col.asc()
        tail = (group_alias.name if user is not None else func.lower(OktaUser.email)).asc()
        return (nulls_order(primary_dir), tail)

    query = query.order_by(*_users_audit_ordering())

    # When `direct` is present and neither `user_id` nor `owner_id` is set,
    # re-apply the order_by using the email/created_at compound shape so
    # the unfiltered "direct only" listing comes back in user-alphabetical
    # order rather than insertion order.
    if q_args.direct is not None and user is None and owner is None:
        if q_args.order_by == AuditOrderBy.moniker:
            primary = func.lower(OktaUser.email)
            primary_dir = primary.desc() if q_args.order_desc else primary.asc()
            query = query.order_by(nulls_order(primary_dir), nullslast(OktaUserGroupMember.created_at.asc()))
        else:
            col = getattr(OktaUserGroupMember, q_args.order_by.value)
            primary_dir = col.desc() if q_args.order_desc else col.asc()
            query = query.order_by(nulls_order(primary_dir), func.lower(OktaUser.email).asc())

    return paginate(
        request,
        query,
        UserGroupAuditPagination,
        item_factory=lambda m: _audit_user_group_row(m, include_role_associations),
        extract=lambda: (q_args.page, q_args.per_page),
    )


@router.get("/groups", name="groups_and_roles")
def groups_and_roles(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchGroupRoleAuditPaginationQuery, Query()],
) -> GroupRoleAuditPagination:
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

    # Free-text `q` — narrow the column set based on which other filter is
    # active so a `?role_id=...&q=...` request searches only associated-
    # group columns (the role is already pinned), and `?group_id=...&q=...`
    # searches only role columns.
    if q_args.q:
        like = f"%{q_args.q}%"
        role_cols = (
            RoleGroup.id.ilike(like),
            RoleGroup.name.ilike(like),
            RoleGroup.description.ilike(like),
        )
        group_cols = (
            group_alias.id.ilike(like),
            group_alias.name.ilike(like),
            group_alias.description.ilike(like),
        )
        if group is not None:
            query = query.filter(_db.or_(*role_cols))
        if role is not None:
            query = query.filter(_db.or_(*group_cols))
        if owner is not None or (group is None and role is None):
            query = query.filter(_db.or_(*role_cols, *group_cols))

    # Compound order_by — the tail tie-breaker keeps page boundaries stable.
    # Primary column depends on context: when `group_id` is pinned, the
    # listing is ordered by role; otherwise it's ordered by the associated
    # group.
    nulls_order = nullsfirst if q_args.order_desc else nullslast

    def _groups_audit_ordering() -> tuple:
        if q_args.order_by == AuditOrderBy.moniker:
            primary = RoleGroup.name if role is None and group is not None else group_alias.name
            primary_dir = primary.desc() if q_args.order_desc else primary.asc()
            return (nulls_order(primary_dir), nullslast(RoleGroupMap.created_at.asc()))
        col = getattr(RoleGroupMap, q_args.order_by.value)
        primary_dir = col.desc() if q_args.order_desc else col.asc()
        tail = (RoleGroup.name if role is None and group is not None else group_alias.name).asc()
        return (nulls_order(primary_dir), tail)

    query = query.order_by(*_groups_audit_ordering())

    return paginate(
        request,
        query,
        GroupRoleAuditPagination,
        item_factory=_audit_group_role_row,
        extract=lambda: (q_args.page, q_args.per_page),
    )
