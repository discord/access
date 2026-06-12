"""Role requests router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from sqlalchemy import String, and_, cast, not_, or_, select
from sqlalchemy.orm import aliased, joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleRequest,
    Tag,
)
from api.models.tag import coalesce_constraints
from api.operations import ApproveRoleRequest, CreateRoleRequest, RejectRoleRequest
from fastapi_pagination.ext.sqlalchemy import paginate

from api.pagination import Page, validated
from api.routers._eager import (
    group_tag_map_options,
    polymorphic_group_options,
    user_group_member_options,
)
from api.schemas import (
    CreateRoleRequestBody,
    ResolveRoleRequestBody,
    RoleRequestDetail,
    RoleRequestSummary,
    SearchRoleRequestQuery,
)

router = APIRouter(prefix="/api/role-requests", tags=["role-requests"])


def _detail_load_options() -> tuple:
    """Eager-load every relationship `RoleRequestDetail` reads.

    `RoleRequest.requester_role.active_user_memberships` and
    `RoleRequest.requested_group.active_group_tags` are populated here
    because the React role-request detail page lists the role's current
    members and any tags on the target group inline."""
    return (
        joinedload(RoleRequest.requester),
        joinedload(RoleRequest.resolver),
        joinedload(RoleRequest.active_resolver),
        selectinload(RoleRequest.requester_role).options(
            *polymorphic_group_options(),
            selectinload(OktaGroup.active_user_memberships).options(*user_group_member_options()),
        ),
        selectinload(RoleRequest.active_requester_role).options(*polymorphic_group_options()),
        selectinload(RoleRequest.requested_group).options(
            *polymorphic_group_options(),
            selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
        ),
        selectinload(RoleRequest.active_requested_group).options(*polymorphic_group_options()),
    )


def _summary_load_options() -> tuple:
    """Slim eager-loads for list / POST / PUT (`RoleRequestSummary`).
    Skips role-member and group-tag loaders that the summary shape
    doesn't expose."""
    return (
        joinedload(RoleRequest.requester),
        joinedload(RoleRequest.resolver),
        joinedload(RoleRequest.active_resolver),
        selectinload(RoleRequest.requester_role).options(*polymorphic_group_options()),
        selectinload(RoleRequest.active_requester_role).options(*polymorphic_group_options()),
        selectinload(RoleRequest.requested_group).options(*polymorphic_group_options()),
        selectinload(RoleRequest.active_requested_group).options(*polymorphic_group_options()),
    )


@router.get("", name="role_requests")
def list_role_requests(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchRoleRequestQuery, Query()],
) -> Page[RoleRequestSummary]:
    from api.auth.permissions import is_access_admin

    stmt = select(RoleRequest).options(*_summary_load_options()).order_by(RoleRequest.created_at.desc())

    if q_args.status:
        stmt = stmt.where(RoleRequest.status == q_args.status)

    if q_args.requester_user_id:
        if q_args.requester_user_id == "@me":
            stmt = stmt.where(RoleRequest.requester_user_id == current_user_id)
        else:
            requester_alias = aliased(OktaUser)
            stmt = stmt.join(RoleRequest.requester.of_type(requester_alias)).where(
                or_(
                    RoleRequest.requester_user_id == q_args.requester_user_id,
                    requester_alias.email.ilike(q_args.requester_user_id),
                )
            )

    if q_args.requester_role_id:
        stmt = stmt.join(RoleRequest.requester_role).where(
            or_(
                RoleRequest.requester_role_id == q_args.requester_role_id,
                RoleGroup.name.ilike(q_args.requester_role_id),
            )
        )

    if q_args.requested_group_id:
        stmt = stmt.join(RoleRequest.requested_group).where(
            or_(
                RoleRequest.requested_group_id == q_args.requested_group_id,
                OktaGroup.name.ilike(q_args.requested_group_id),
            )
        )

    if q_args.assignee_user_id:
        # "Requests I can resolve" — same admin/non-admin branching as Flask.
        # Admins see every PENDING request whose target group has the
        # `disallow_self_add_*` tag and where every existing owner is also a
        # member of the requester role (otherwise approval is impossible).
        # Non-admins are restricted to groups they own, and pre-filtered to
        # exclude requests they can't resolve due to those same tags.
        assignee_user_id = current_user_id if q_args.assignee_user_id == "@me" else q_args.assignee_user_id
        assignee_user = db.scalars(
            select(OktaUser).where(or_(OktaUser.id == assignee_user_id, OktaUser.email.ilike(assignee_user_id)))
        ).first()
        if assignee_user is not None:
            groups_owned_subquery = (
                select(OktaGroup.id)
                .options(selectinload(OktaGroup.active_user_ownerships))
                .join(OktaGroup.active_user_ownerships)
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaUserGroupMember.user_id == assignee_user.id)
                .subquery()
            )
            owner_app_group_alias = aliased(AppGroup)
            app_groups_owned_subquery = (
                select(AppGroup.id)
                .options(
                    joinedload(AppGroup.app)
                    .joinedload(App.active_owner_app_groups.of_type(owner_app_group_alias))
                    .selectinload(owner_app_group_alias.active_user_ownerships)
                )
                .join(AppGroup.app)
                .join(App.active_owner_app_groups.of_type(owner_app_group_alias))
                .join(owner_app_group_alias.active_user_ownerships)
                .where(AppGroup.deleted_at.is_(None))
                .where(OktaUserGroupMember.user_id == assignee_user.id)
                .subquery()
            )

            if is_access_admin(db, assignee_user.id):
                # Pending role requests for ownership / membership where the
                # target group is tagged `disallow_self_add_*` and every
                # existing owner is in the requester role's membership.
                tagged_owner_requests = [
                    rr
                    for rr in db.scalars(
                        select(RoleRequest)
                        .options(
                            joinedload(RoleRequest.requester_role).options(
                                selectinload(OktaGroup.active_user_memberships)
                            ),
                            joinedload(RoleRequest.requested_group).options(
                                selectinload(OktaGroup.active_group_tags),
                                selectinload(OktaGroup.active_user_ownerships),
                            ),
                        )
                        .where(RoleRequest.status == AccessRequestStatus.PENDING)
                        .where(RoleRequest.request_ownership.is_(True))
                    ).all()
                    if coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                        tags=[tm.active_tag for tm in rr.requested_group.active_group_tags],
                    )
                ]
                tagged_member_requests = [
                    rr
                    for rr in db.scalars(
                        select(RoleRequest)
                        .options(
                            joinedload(RoleRequest.requester_role).options(
                                selectinload(OktaGroup.active_user_memberships)
                            ),
                            joinedload(RoleRequest.requested_group).options(
                                selectinload(OktaGroup.active_group_tags),
                                selectinload(OktaGroup.active_user_ownerships),
                            ),
                        )
                        .where(RoleRequest.status == AccessRequestStatus.PENDING)
                        .where(RoleRequest.request_ownership.is_(False))
                    ).all()
                    if coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                        tags=[tm.active_tag for tm in rr.requested_group.active_group_tags],
                    )
                ]

                blocked_request_ids: list[str] = []
                for req in tagged_owner_requests + tagged_member_requests:
                    role_member_ids = [m.user_id for m in req.requester_role.active_user_memberships]
                    if all(o.user_id in role_member_ids for o in req.requested_group.active_user_ownerships):
                        blocked_request_ids.append(req.id)

                stmt = stmt.join(RoleRequest.requested_group).where(
                    or_(
                        OktaGroup.id.in_(groups_owned_subquery),
                        OktaGroup.id.in_(app_groups_owned_subquery),
                        RoleRequest.id.in_(blocked_request_ids),
                    )
                )
            else:
                stmt = stmt.join(RoleRequest.requested_group).where(
                    or_(
                        OktaGroup.id.in_(groups_owned_subquery),
                        OktaGroup.id.in_(app_groups_owned_subquery),
                    )
                )

                owned_groups = (
                    db.scalars(
                        select(OktaGroup)
                        .options(joinedload(OktaGroup.active_group_tags))
                        .where(
                            or_(
                                OktaGroup.id.in_(groups_owned_subquery),
                                OktaGroup.id.in_(app_groups_owned_subquery),
                            )
                        )
                    )
                    .unique()
                    .all()
                )
                owned_groups_no_self_owner = [
                    g.id
                    for g in owned_groups
                    if coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                        tags=[tm.active_tag for tm in g.active_group_tags],
                    )
                ]
                owned_groups_no_self_member = [
                    g.id
                    for g in owned_groups
                    if coalesce_constraints(
                        constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                        tags=[tm.active_tag for tm in g.active_group_tags],
                    )
                ]
                role_membership_ids = [
                    rg.id
                    for rg in db.scalars(select(RoleGroup).options(joinedload(RoleGroup.active_user_memberships)))
                    .unique()
                    .all()
                    if assignee_user.id in [m.user_id for m in rg.active_user_memberships]
                ]
                stmt = stmt.where(
                    not_(
                        or_(
                            and_(
                                RoleRequest.requested_group_id.in_(owned_groups_no_self_owner),
                                RoleRequest.requester_role_id.in_(role_membership_ids),
                                RoleRequest.request_ownership.is_(True),
                            ),
                            and_(
                                RoleRequest.requested_group_id.in_(owned_groups_no_self_member),
                                RoleRequest.requester_role_id.in_(role_membership_ids),
                                RoleRequest.request_ownership.is_(False),
                            ),
                        )
                    )
                )
            # Whether admin or not, never include the assignee's own requests.
            stmt = stmt.where(RoleRequest.requester_user_id != assignee_user.id)
        else:
            stmt = stmt.where(False)

    if q_args.resolver_user_id:
        if q_args.resolver_user_id == "@me":
            stmt = stmt.where(RoleRequest.resolver_user_id == current_user_id)
        else:
            resolver_alias = aliased(OktaUser)
            stmt = stmt.outerjoin(RoleRequest.resolver.of_type(resolver_alias)).where(
                or_(
                    RoleRequest.resolver_user_id == q_args.resolver_user_id,
                    resolver_alias.email.ilike(q_args.resolver_user_id),
                )
            )

    # Free-text search over id prefix, status, requester / resolver
    # name+email, role name+description, requested-group name+description.
    if q_args.q:
        like = f"%{q_args.q}%"
        q_requester_alias = aliased(OktaUser)
        q_resolver_alias = aliased(OktaUser)
        q_role_alias = aliased(OktaGroup)
        q_group_alias = aliased(OktaGroup)
        stmt = (
            stmt.join(RoleRequest.requester.of_type(q_requester_alias))
            .join(RoleRequest.requester_role.of_type(q_role_alias))
            .join(RoleRequest.requested_group.of_type(q_group_alias))
            .outerjoin(RoleRequest.resolver.of_type(q_resolver_alias))
            .where(
                or_(
                    RoleRequest.id.like(f"{q_args.q}%"),
                    cast(RoleRequest.status, String).ilike(like),
                    q_requester_alias.email.ilike(like),
                    q_requester_alias.first_name.ilike(like),
                    q_requester_alias.last_name.ilike(like),
                    q_requester_alias.display_name.ilike(like),
                    (q_requester_alias.first_name + " " + q_requester_alias.last_name).ilike(like),
                    q_role_alias.name.ilike(like),
                    q_role_alias.description.ilike(like),
                    q_group_alias.name.ilike(like),
                    q_group_alias.description.ilike(like),
                    q_resolver_alias.email.ilike(like),
                    q_resolver_alias.first_name.ilike(like),
                    q_resolver_alias.last_name.ilike(like),
                    q_resolver_alias.display_name.ilike(like),
                    (q_resolver_alias.first_name + " " + q_resolver_alias.last_name).ilike(like),
                )
            )
        )

    return paginate(db, stmt, transformer=validated(RoleRequestSummary))


@router.get("/{role_request_id}", name="role_request_by_id")
def get_role_request(role_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> RoleRequestDetail:
    rr = db.scalars(
        select(RoleRequest).options(*_detail_load_options()).where(RoleRequest.id == role_request_id)
    ).first()
    if rr is None:
        raise HTTPException(404, "Not Found")
    return RoleRequestDetail.model_validate(rr, from_attributes=True)


@router.post("", name="role_requests_create", status_code=201)
def post_role_request(
    body: CreateRoleRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> RoleRequestSummary:
    from api.auth import permissions as _perms

    requester = db.scalars(
        select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
    ).first()
    role = db.scalars(
        select(RoleGroup).where(RoleGroup.deleted_at.is_(None)).where(RoleGroup.id == body.role_id)
    ).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    if requester is None or not _perms.can_manage_group(db, current_user_id, role):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    group = db.scalars(
        select(OktaGroup).where(OktaGroup.deleted_at.is_(None)).where(OktaGroup.id == body.group_id)
    ).first()
    if group is None:
        raise HTTPException(404, "Not Found")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")
    if type(group) is RoleGroup:
        raise HTTPException(400, "Role requests may only be made for groups and app groups (not roles).")

    # Close any existing pending duplicate requests
    existing = db.scalars(
        select(RoleRequest)
        .where(RoleRequest.requester_user_id == current_user_id)
        .where(RoleRequest.requester_role_id == body.role_id)
        .where(RoleRequest.requested_group_id == body.group_id)
        .where(RoleRequest.request_ownership == body.group_owner)
        .where(RoleRequest.status == AccessRequestStatus.PENDING)
        .where(RoleRequest.resolved_at.is_(None))
    ).all()
    for old in existing:
        RejectRoleRequest(
            role_request=old,
            rejection_reason="Closed due to duplicate role request creation",
            notify_requester=False,
            current_user_id=current_user_id,
        ).execute()
    rr = CreateRoleRequest(
        requester_user=requester,
        requester_role=role,
        requested_group=group,
        request_ownership=body.group_owner,
        request_reason=body.reason or "",
        request_ending_at=body.ending_at,
    ).execute()
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    db.expire_all()
    refreshed = db.scalars(select(RoleRequest).options(*_summary_load_options()).where(RoleRequest.id == rr.id)).first()
    return RoleRequestSummary.model_validate(refreshed or rr, from_attributes=True)


@router.put("/{role_request_id}", name="role_request_by_id_put")
def put_role_request(
    role_request_id: str,
    body: ResolveRoleRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> RoleRequestSummary:
    from api.auth import permissions as _perms
    from api.operations.constraints import CheckForReason

    rr = db.scalars(
        select(RoleRequest).options(*_summary_load_options()).where(RoleRequest.id == role_request_id)
    ).first()
    if rr is None:
        raise HTTPException(404, "Not Found")

    # Requester can always reject their own request, but cannot approve it.
    if rr.requester_user_id == current_user_id:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not _perms.can_manage_group(db, current_user_id, rr.active_requested_group):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    # Tags on the requester role can require a justification before approval.
    # Note `CheckForReason.execute_for_role()` checks the *role's* tags (not
    # the target group's), and the members/owners lists carry the target
    # group id (not user ids) — see Flask api/views/resources/role_request.py.
    if body.approved:
        valid, err_message = CheckForReason(
            group=rr.requester_role_id,
            reason=body.reason,
            members_to_add=[rr.requested_group_id] if not rr.request_ownership else [],
            owners_to_add=[rr.requested_group_id] if rr.request_ownership else [],
        ).execute_for_role()
        if not valid:
            raise HTTPException(400, err_message)

    if rr.status != AccessRequestStatus.PENDING or rr.resolved_at is not None:
        raise HTTPException(409, "Role request is not pending")
    if body.approved:
        if not rr.requested_group.is_managed:
            raise HTTPException(400, "Groups not managed by Access cannot be modified")
        ApproveRoleRequest(
            role_request=rr,
            approver_user=current_user_id,
            approval_reason=body.reason or "",
            ending_at=body.ending_at,
        ).execute()
    else:
        RejectRoleRequest(
            role_request=rr,
            current_user_id=current_user_id,
            rejection_reason=body.reason or "",
            notify_requester=rr.requester_user_id != current_user_id,
        ).execute()
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    db.expire_all()
    refreshed = db.scalars(
        select(RoleRequest).options(*_summary_load_options()).where(RoleRequest.id == role_request_id)
    ).first()
    return RoleRequestSummary.model_validate(refreshed, from_attributes=True)
