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

from sqlalchemy import and_, func, not_, nullsfirst, nullslast, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import aliased, joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import (
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from fastapi_pagination.ext.sqlalchemy import paginate

from api.pagination import Page
from api.schemas import (
    AuditOrderBy,
    SearchGroupRoleAuditQuery,
    SearchUserGroupAuditQuery,
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
from api.schemas.core_schemas import AppSummary, AppTagMapDetail, OktaGroupTagMapDetail, TagSummary

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
    user = db.scalars(
        select(OktaUser)
        .where(or_(OktaUser.id == value, OktaUser.email.ilike(value)))
        .order_by(nullsfirst(OktaUser.deleted_at.desc()))
    ).first()
    if user is None:
        raise HTTPException(404, "Not Found")
    return user


def _resolve_group(db: DbSession, value: str | None) -> OktaGroup | None:
    if value is None:
        return None
    group = db.scalars(
        select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
        .where(or_(OktaGroup.id == value, OktaGroup.name == value))
        .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
    ).first()
    if group is None:
        raise HTTPException(404, "Not Found")
    return group


def _resolve_role(db: DbSession, value: str | None) -> RoleGroup | None:
    if value is None:
        return None
    role = db.scalars(
        select(RoleGroup)
        .where(or_(RoleGroup.id == value, RoleGroup.name == value))
        .order_by(nullsfirst(RoleGroup.deleted_at.desc()))
    ).first()
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
    # `active_group_tags` is `lazy="raise_on_sql"`. Only emit the tag rows when
    # the relationship has been eager-loaded; otherwise leave the list empty.
    # The OktaGroupTagMap rows back-reference the parent group (a self-loop)
    # and the AppTagMap rows carry `lazy="raise_on_sql"` relationships that
    # aren't warmed in this scope, so build both models by hand and skip the
    # fields that would touch them.
    state = sa_inspect(g)
    active_group_tags: list[OktaGroupTagMapDetail] = []
    if state is not None and "active_group_tags" not in state.unloaded:
        for row in getattr(g, "active_group_tags", None) or []:
            active_tag = getattr(row, "active_tag", None)
            active_app_tag_mapping = getattr(row, "active_app_tag_mapping", None)
            atm_payload: AppTagMapDetail | None = None
            if active_app_tag_mapping is not None:
                active_app = getattr(active_app_tag_mapping, "active_app", None)
                atm_payload = AppTagMapDetail(
                    created_at=active_app_tag_mapping.created_at,
                    ended_at=active_app_tag_mapping.ended_at,
                    active_app=AppSummary.model_validate(active_app, from_attributes=True) if active_app else None,
                )
            active_group_tags.append(
                OktaGroupTagMapDetail(
                    created_at=row.created_at,
                    ended_at=row.ended_at,
                    active_tag=TagSummary.model_validate(active_tag, from_attributes=True) if active_tag else None,
                    active_app_tag_mapping=atm_payload,
                )
            )
    return _GroupRefForAudit(
        id=g.id,
        type=g.type,
        name=g.name,
        is_owner=getattr(g, "is_owner", None),
        is_managed=getattr(g, "is_managed", None),
        deleted_at=g.deleted_at,
        app=_app_ref_for_audit(getattr(g, "app", None)) if isinstance(g, AppGroup) else None,
        active_group_tags=active_group_tags,
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
        group=_group_ref_for_audit(getattr(m, "group", None), include_role_associations),
        role_group_mapping=_role_group_mapping_for_audit(getattr(m, "role_group_mapping", None)),
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
        role_group=_role_group_ref_for_audit(getattr(rgm, "role_group", None)),
        created_actor=_user_summary_for_audit(getattr(rgm, "created_actor", None)),
        ended_actor=_user_summary_for_audit(getattr(rgm, "ended_actor", None)),
    )


# --- Routes -----------------------------------------------------------------


@router.get("/users", name="users_and_groups")
def users_and_groups(
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchUserGroupAuditQuery, Query()],
) -> Page[AuditUserGroupRow]:
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
            joinedload(OktaGroupTagMap.active_app_tag_mapping).joinedload(AppTagMap.active_app),
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

    stmt = (
        select(OktaUserGroupMember)
        .options(
            joinedload(OktaUserGroupMember.user),
            joinedload(OktaUserGroupMember.created_actor),
            joinedload(OktaUserGroupMember.ended_actor),
            joinedload(OktaUserGroupMember.access_request),
            group_load,
            selectinload(OktaUserGroupMember.role_group_mapping).joinedload(RoleGroupMap.role_group),
        )
        .join(OktaUserGroupMember.user)
        .join(OktaUserGroupMember.group.of_type(group_alias))
    )

    if user is not None:
        stmt = stmt.where(OktaUserGroupMember.user_id == user.id)
    if group is not None:
        stmt = stmt.where(OktaUserGroupMember.group_id == group.id)

    # Owner filter — only return memberships in groups owned by `owner`,
    # either directly or transitively via the owning app.
    if owner is not None:
        owner_group_ids = db.scalars(
            select(OktaUserGroupMember.group_id)
            .where(OktaUserGroupMember.user_id == owner.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        ).all()
        app_owner_group_ids = [
            ag.id
            for ag in db.scalars(
                select(AppGroup).where(AppGroup.id.in_(owner_group_ids)).where(AppGroup.is_owner.is_(True))
            ).all()
        ]
        app_ids = [ag.app_id for ag in db.scalars(select(AppGroup).where(AppGroup.id.in_(app_owner_group_ids))).all()]
        app_groups_owned_ids = [
            ag.id
            for ag in db.scalars(
                select(AppGroup).where(AppGroup.app_id.in_(app_ids)).where(AppGroup.deleted_at.is_(None))
            ).all()
        ]
        if q_args.app_owner is True:
            stmt = stmt.where(
                or_(
                    OktaUserGroupMember.group_id.in_(owner_group_ids),
                    OktaUserGroupMember.group_id.in_(app_groups_owned_ids),
                )
            )
        else:
            # Exclude app groups that have at least one direct owner who isn't `owner`.
            directly_owned_by_others_ids = db.scalars(
                select(OktaUserGroupMember.group_id)
                .where(OktaUserGroupMember.group_id.in_(app_groups_owned_ids))
                .where(OktaUserGroupMember.user_id != owner.id)
                .where(OktaUserGroupMember.is_owner.is_(True))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
            ).all()
            visible_app_groups = set(app_groups_owned_ids) - set(directly_owned_by_others_ids)
            stmt = stmt.where(
                or_(
                    OktaUserGroupMember.group_id.in_(owner_group_ids),
                    OktaUserGroupMember.group_id.in_(visible_app_groups),
                )
            )
        # When filtering by `owner_id`, exclude the owner's own memberships:
        # the frontend's "expiring access" review page expects to see *other*
        # users whose access the owner needs to renew, not the owner.
        stmt = stmt.where(OktaUserGroupMember.user_id != owner.id)

    if q_args.owner is not None:
        stmt = stmt.where(OktaUserGroupMember.is_owner == q_args.owner)

    if q_args.active is True:
        stmt = stmt.where(
            or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > func.now(),
            )
        )
    elif q_args.active is False:
        stmt = stmt.where(
            and_(
                OktaUserGroupMember.ended_at.is_not(None),
                OktaUserGroupMember.ended_at < func.now(),
            )
        )

    if q_args.needs_review is True:
        stmt = stmt.where(OktaUserGroupMember.should_expire.is_(False))

    if q_args.direct is True:
        stmt = stmt.where(not_(OktaUserGroupMember.active_role_group_mapping.has()))

    if q_args.deleted is False:
        stmt = stmt.where(OktaUser.deleted_at.is_(None))

    if q_args.managed is not None:
        stmt = stmt.where(group_alias.is_managed == q_args.managed)

    if q_args.start_date is not None and q_args.end_date is not None:
        stmt = stmt.where(
            and_(
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
            stmt = stmt.where(or_(*group_cols))
        if group is not None:
            stmt = stmt.where(or_(*user_cols))
        if owner is not None or (user is None and group is None):
            stmt = stmt.where(or_(*group_cols, *user_cols))

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

    stmt = stmt.order_by(*_users_audit_ordering())

    # When `direct` is present and neither `user_id` nor `owner_id` is set,
    # re-apply the order_by using the email/created_at compound shape so
    # the unfiltered "direct only" listing comes back in user-alphabetical
    # order rather than insertion order.
    if q_args.direct is not None and user is None and owner is None:
        if q_args.order_by == AuditOrderBy.moniker:
            primary = func.lower(OktaUser.email)
            primary_dir = primary.desc() if q_args.order_desc else primary.asc()
            stmt = stmt.order_by(nulls_order(primary_dir), nullslast(OktaUserGroupMember.created_at.asc()))
        else:
            col = getattr(OktaUserGroupMember, q_args.order_by.value)
            primary_dir = col.desc() if q_args.order_desc else col.asc()
            stmt = stmt.order_by(nulls_order(primary_dir), func.lower(OktaUser.email).asc())

    return paginate(
        db,
        stmt,
        transformer=lambda items: [_audit_user_group_row(m, include_role_associations) for m in items],
    )


@router.get("/groups", name="groups_and_roles")
def groups_and_roles(
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchGroupRoleAuditQuery, Query()],
) -> Page[AuditGroupRoleRow]:
    from api.auth.permissions import is_access_admin

    role_id = _resolve_me(q_args.role_id, current_user_id)
    owner_id = _resolve_me(q_args.owner_id, current_user_id)
    role_owner_id = _resolve_me(q_args.role_owner_id, current_user_id)
    role = _resolve_role(db, role_id)
    group = _resolve_group(db, q_args.group_id)
    owner = _resolve_user(db, owner_id)
    role_owner = _resolve_user(db, role_owner_id)

    group_alias = aliased(OktaGroup)

    stmt = (
        select(RoleGroupMap)
        .options(
            joinedload(RoleGroupMap.role_group),
            joinedload(RoleGroupMap.created_actor),
            joinedload(RoleGroupMap.ended_actor),
            selectinload(RoleGroupMap.group).options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
                selectinload(OktaGroup.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_tag),
                    joinedload(OktaGroupTagMap.active_app_tag_mapping).joinedload(AppTagMap.active_app),
                ),
            ),
        )
        .join(RoleGroupMap.role_group)
        .join(RoleGroupMap.group.of_type(group_alias))
    )

    if role is not None:
        stmt = stmt.where(RoleGroupMap.role_group_id == role.id)
    if group is not None:
        stmt = stmt.where(RoleGroupMap.group_id == group.id)

    if owner is not None:
        owner_group_ids = db.scalars(
            select(OktaUserGroupMember.group_id)
            .where(OktaUserGroupMember.user_id == owner.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        ).all()
        app_owner_group_ids = [
            ag.id
            for ag in db.scalars(
                select(AppGroup).where(AppGroup.id.in_(owner_group_ids)).where(AppGroup.is_owner.is_(True))
            ).all()
        ]
        app_ids = [ag.app_id for ag in db.scalars(select(AppGroup).where(AppGroup.id.in_(app_owner_group_ids))).all()]
        app_groups_owned_ids = [
            ag.id
            for ag in db.scalars(
                select(AppGroup).where(AppGroup.app_id.in_(app_ids)).where(AppGroup.deleted_at.is_(None))
            ).all()
        ]
        if q_args.app_owner is True:
            stmt = stmt.where(
                or_(
                    RoleGroupMap.group_id.in_(owner_group_ids),
                    RoleGroupMap.group_id.in_(app_groups_owned_ids),
                )
            )
        else:
            directly_owned_by_others_ids = db.scalars(
                select(OktaUserGroupMember.group_id)
                .where(OktaUserGroupMember.group_id.in_(app_groups_owned_ids))
                .where(OktaUserGroupMember.user_id != owner.id)
                .where(OktaUserGroupMember.is_owner.is_(True))
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
            ).all()
            visible_app_groups = set(app_groups_owned_ids) - set(directly_owned_by_others_ids)
            stmt = stmt.where(
                or_(
                    RoleGroupMap.group_id.in_(owner_group_ids),
                    RoleGroupMap.group_id.in_(visible_app_groups),
                )
            )

    if role_owner is not None:
        role_owner_role_ids = db.scalars(
            select(OktaUserGroupMember.group_id)
            .where(OktaUserGroupMember.user_id == role_owner.id)
            .where(OktaUserGroupMember.is_owner.is_(True))
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
        ).all()
        # Access admins additionally see roles that have NO active owner —
        # those are the roles only an admin can resolve expiring access for.
        unowned_admin_role_ids: list[str] = []
        if is_access_admin(db, role_owner.id):
            owners_subquery = (
                select(OktaUserGroupMember.group_id)
                .where(
                    and_(
                        OktaUserGroupMember.is_owner.is_(True),
                        or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > func.now(),
                        ),
                    )
                )
                .subquery()
            )
            unowned_admin_role_ids = [
                rg.id
                for rg in db.scalars(
                    select(RoleGroup).where(and_(RoleGroup.deleted_at.is_(None), ~RoleGroup.id.in_(owners_subquery)))
                ).all()
            ]
        stmt = stmt.where(
            or_(
                RoleGroupMap.role_group_id.in_(role_owner_role_ids),
                RoleGroupMap.role_group_id.in_(unowned_admin_role_ids),
            )
        )

    if q_args.owner is not None:
        stmt = stmt.where(RoleGroupMap.is_owner == q_args.owner)

    if q_args.active is True:
        stmt = stmt.where(
            or_(
                RoleGroupMap.ended_at.is_(None),
                RoleGroupMap.ended_at > func.now(),
            )
        )
    elif q_args.active is False:
        stmt = stmt.where(
            and_(
                RoleGroupMap.ended_at.is_not(None),
                RoleGroupMap.ended_at < func.now(),
            )
        )

    if q_args.needs_review is True:
        stmt = stmt.where(RoleGroupMap.should_expire.is_(False))

    if q_args.managed is not None:
        stmt = stmt.where(group_alias.is_managed == q_args.managed)

    if q_args.start_date is not None and q_args.end_date is not None:
        stmt = stmt.where(
            and_(
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
            stmt = stmt.where(or_(*role_cols))
        if role is not None:
            stmt = stmt.where(or_(*group_cols))
        if owner is not None or (group is None and role is None):
            stmt = stmt.where(or_(*role_cols, *group_cols))

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

    stmt = stmt.order_by(*_groups_audit_ordering())

    return paginate(
        db,
        stmt,
        transformer=lambda items: [_audit_group_role_row(rgm) for rgm in items],
    )
