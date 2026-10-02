"""Role requests router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy import String, cast, false, or_, select
from sqlalchemy.orm import aliased, joinedload, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import (
    AccessRequestStatus,
    OktaGroup,
    OktaUser,
    RoleGroup,
    RoleRequest,
)
from api.models.request_reviewers import assigned_reviewer_condition
from api.operations import ApproveRoleRequest, CreateRoleRequest, RejectRoleRequest
from fastapi_pagination.ext.sqlalchemy import apaginate

from api.pagination import Page, validated
from api.routers._eager import (
    group_tag_map_options,
    polymorphic_group_options,
    user_group_member_options,
)
from api.routers._fan_out import defer_fan_out
from api.routers._reviewers import request_reviewers_response
from api.schemas import (
    CreateRoleRequestBody,
    RequestReviewers,
    ResolveRoleRequestBody,
    RoleRequestDetail,
    RoleRequestSummary,
    SearchRoleRequestQuery,
)

router = APIRouter(prefix="/api/role-requests", tags=["role-requests"], dependencies=[Depends(defer_fan_out)])


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
async def list_role_requests(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchRoleRequestQuery, Query()],
) -> Page[RoleRequestSummary]:
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
        # Requests whose assigned reviewers include the assignee; see
        # api/models/request_reviewers.py.
        assignee_user_id = current_user_id if q_args.assignee_user_id == "@me" else q_args.assignee_user_id
        assignee_user = (
            await db.scalars(
                select(OktaUser).where(or_(OktaUser.id == assignee_user_id, OktaUser.email.ilike(assignee_user_id)))
            )
        ).first()
        if assignee_user is not None:
            stmt = stmt.where(assigned_reviewer_condition(RoleRequest, assignee_user.id))
        else:
            stmt = stmt.where(false())

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

    return await apaginate(db, stmt, transformer=validated(RoleRequestSummary))


@router.get("/{role_request_id}", name="role_request_by_id")
async def get_role_request(role_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> RoleRequestDetail:
    rr = (
        await db.scalars(select(RoleRequest).options(*_detail_load_options()).where(RoleRequest.id == role_request_id))
    ).first()
    if rr is None:
        raise HTTPException(404, "Not Found")
    return RoleRequestDetail.model_validate(rr, from_attributes=True)


@router.get("/{role_request_id}/reviewers", name="role_request_reviewers")
async def get_role_request_reviewers(
    role_request_id: str, db: DbSession, current_user_id: CurrentUserId
) -> RequestReviewers:
    """Possible reviewers of a role request by owner level, and the assigned level."""
    request = await db.get(RoleRequest, role_request_id)
    if request is None:
        raise HTTPException(404, "Not Found")
    return await request_reviewers_response(request)


@router.post("", name="role_requests_create", status_code=201)
async def post_role_request(
    body: CreateRoleRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> RoleRequestSummary:
    from api.auth import permissions as _perms

    requester = (
        await db.scalars(select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id))
    ).first()
    role = (
        await db.scalars(select(RoleGroup).where(RoleGroup.deleted_at.is_(None)).where(RoleGroup.id == body.role_id))
    ).first()
    if role is None:
        raise HTTPException(404, "Not Found")
    if requester is None or not await _perms.can_manage_group(db, current_user_id, role):
        raise HTTPException(403, "Current user is not allowed to perform this action")
    group = (
        await db.scalars(select(OktaGroup).where(OktaGroup.deleted_at.is_(None)).where(OktaGroup.id == body.group_id))
    ).first()
    if group is None:
        raise HTTPException(404, "Not Found")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")
    if type(group) is RoleGroup:
        raise HTTPException(400, "Role requests may only be made for groups and app groups (not roles).")

    # Close any existing pending duplicate requests
    existing = (
        await db.scalars(
            select(RoleRequest)
            .where(RoleRequest.requester_user_id == current_user_id)
            .where(RoleRequest.requester_role_id == body.role_id)
            .where(RoleRequest.requested_group_id == body.group_id)
            .where(RoleRequest.request_ownership == body.group_owner)
            .where(RoleRequest.status == AccessRequestStatus.PENDING)
            .where(RoleRequest.resolved_at.is_(None))
        )
    ).all()
    for old in existing:
        await RejectRoleRequest(
            role_request=old,
            rejection_reason="Closed due to duplicate role request creation",
            notify_requester=False,
            current_user_id=current_user_id,
        ).execute()
    rr = await CreateRoleRequest(
        requester_user=requester,
        requester_role=role,
        requested_group=group,
        request_ownership=body.group_owner,
        request_reason=body.reason or "",
        request_ending_at=body.ending_at,
    ).execute()
    # CreateRoleRequest.execute() returns None only for an unmanaged or role
    # target group; both are rejected upstream in this handler, so a request
    # is always created here.
    assert rr is not None
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    rr_id = rr.id
    db.expire_all()
    refreshed = (
        await db.scalars(select(RoleRequest).options(*_summary_load_options()).where(RoleRequest.id == rr_id))
    ).first()
    return RoleRequestSummary.model_validate(refreshed or rr, from_attributes=True)


@router.put("/{role_request_id}", name="role_request_by_id_put")
async def put_role_request(
    role_request_id: str,
    body: ResolveRoleRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> RoleRequestSummary:
    from api.auth import permissions as _perms
    from api.operations.constraints import CheckForReason

    rr = (
        await db.scalars(select(RoleRequest).options(*_summary_load_options()).where(RoleRequest.id == role_request_id))
    ).first()
    if rr is None:
        raise HTTPException(404, "Not Found")

    # Requester can always reject their own request, but cannot approve it.
    if rr.requester_user_id == current_user_id:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not await _perms.can_manage_group(db, current_user_id, rr.active_requested_group):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    # Tags on the requester role can require a justification before approval.
    # Note `CheckForReason.execute_for_role()` checks the *role's* tags (not
    # the target group's), and the members/owners lists carry the target
    # group id (not user ids) — see Flask api/views/resources/role_request.py.
    if body.approved:
        valid, err_message = await CheckForReason(
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
        await ApproveRoleRequest(
            role_request=rr,
            approver_user=current_user_id,
            approval_reason=body.reason or "",
            ending_at=body.ending_at,
        ).execute()
    else:
        await RejectRoleRequest(
            role_request=rr,
            current_user_id=current_user_id,
            rejection_reason=body.reason or "",
            notify_requester=rr.requester_user_id != current_user_id,
        ).execute()
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    db.expire_all()
    refreshed = (
        await db.scalars(select(RoleRequest).options(*_summary_load_options()).where(RoleRequest.id == role_request_id))
    ).first()
    return RoleRequestSummary.model_validate(refreshed, from_attributes=True)
