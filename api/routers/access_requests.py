"""Access requests router."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import TypeAdapter
from sqlalchemy import String, cast
from sqlalchemy.orm import aliased, joinedload, selectin_polymorphic, selectinload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
)
from api.operations import ApproveAccessRequest, CreateAccessRequest, RejectAccessRequest
from api.pagination import paginate
from api.routers._eager import group_tag_map_options, role_group_map_options
from api.schemas import (
    AccessRequestDetail,
    CreateAccessRequestBody,
    ResolveAccessRequestBody,
    SearchAccessRequestPaginationQuery,
)
from api.schemas._serialize import dump_orm

router = APIRouter(prefix="/api/requests", tags=["access-requests"])

_adapter = TypeAdapter(AccessRequestDetail)


# Eager-load options for the access-request POST response refetch — chains
# the polymorphic group + tag + role-association loaders under
# `AccessRequest.requested_group` so the response serialization (and any
# plugin hook walking these relationships) doesn't N+1 row-by-row when the
# requested group is a Role.
def _post_load_options() -> tuple:
    requested_group_load = selectinload(AccessRequest.requested_group).options(
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AppGroup.app),
        selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
        selectinload(RoleGroup.active_role_associated_group_member_mappings).options(*role_group_map_options()),
        selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(*role_group_map_options()),
    )
    return (
        joinedload(AccessRequest.requester),
        joinedload(AccessRequest.active_requester),
        requested_group_load,
        selectinload(AccessRequest.active_requested_group),
        joinedload(AccessRequest.resolver),
        joinedload(AccessRequest.active_resolver),
    )


def _load_options() -> tuple:
    return (
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AccessRequest.requester),
        joinedload(AccessRequest.active_requester),
        selectinload(AccessRequest.requested_group),
        selectinload(AccessRequest.active_requested_group),
        joinedload(AccessRequest.resolver),
        joinedload(AccessRequest.active_resolver),
    )


@router.get("", name="access_requests")
def list_access_requests(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchAccessRequestPaginationQuery, Query()],
) -> dict[str, Any]:
    query = db.query(AccessRequest).options(*_load_options()).order_by(AccessRequest.created_at.desc())

    # Honored search filters: status, requester_user_id, requested_group_id,
    # assignee_user_id, resolver_user_id. The frontend sends these from the
    # URL bar on the requests list page; without them the client-side
    # filters do nothing.
    if q_args.status:
        query = query.filter(AccessRequest.status == q_args.status)

    if q_args.requester_user_id:
        if q_args.requester_user_id == "@me":
            query = query.filter(AccessRequest.requester_user_id == current_user_id)
        else:
            requester_alias = aliased(OktaUser)
            query = query.join(AccessRequest.requester.of_type(requester_alias)).filter(
                _db.or_(
                    AccessRequest.requester_user_id == q_args.requester_user_id,
                    requester_alias.email.ilike(q_args.requester_user_id),
                )
            )

    if q_args.requested_group_id:
        query = query.join(AccessRequest.requested_group).filter(
            _db.or_(
                AccessRequest.requested_group_id == q_args.requested_group_id,
                OktaGroup.name.ilike(q_args.requested_group_id),
            )
        )

    if q_args.assignee_user_id:
        assignee_user_id = current_user_id if q_args.assignee_user_id == "@me" else q_args.assignee_user_id
        assignee_user = (
            db.query(OktaUser)
            .filter(_db.or_(OktaUser.id == assignee_user_id, OktaUser.email.ilike(assignee_user_id)))
            .first()
        )
        if assignee_user is not None:
            groups_owned_subquery = (
                db.query(OktaGroup.id)
                .options(selectinload(OktaGroup.active_user_ownerships))
                .join(OktaGroup.active_user_ownerships)
                .filter(OktaGroup.deleted_at.is_(None))
                .filter(OktaUserGroupMember.user_id == assignee_user.id)
                .subquery()
            )
            owner_app_group_alias = aliased(AppGroup)
            app_groups_owned_subquery = (
                db.query(AppGroup.id)
                .options(
                    joinedload(AppGroup.app)
                    .joinedload(App.active_owner_app_groups.of_type(owner_app_group_alias))
                    .selectinload(owner_app_group_alias.active_user_ownerships)
                )
                .join(AppGroup.app)
                .join(App.active_owner_app_groups.of_type(owner_app_group_alias))
                .join(owner_app_group_alias.active_user_ownerships)
                .filter(AppGroup.deleted_at.is_(None))
                .filter(OktaUserGroupMember.user_id == assignee_user.id)
                .subquery()
            )
            query = query.join(AccessRequest.requested_group).filter(
                _db.or_(
                    OktaGroup.id.in_(groups_owned_subquery),
                    OktaGroup.id.in_(app_groups_owned_subquery),
                )
            )
        else:
            query = query.filter(False)

    if q_args.resolver_user_id:
        if q_args.resolver_user_id == "@me":
            query = query.filter(AccessRequest.resolver_user_id == current_user_id)
        else:
            resolver_alias = aliased(OktaUser)
            query = query.outerjoin(AccessRequest.resolver.of_type(resolver_alias)).filter(
                _db.or_(
                    AccessRequest.resolver_user_id == q_args.resolver_user_id,
                    resolver_alias.email.ilike(q_args.resolver_user_id),
                )
            )

    # Free-text search: id prefix, status, requester/resolver name+email,
    # requested group name+description.
    if q_args.q:
        like = f"%{q_args.q}%"
        q_requester_alias = aliased(OktaUser)
        q_resolver_alias = aliased(OktaUser)
        query = (
            query.join(AccessRequest.requester.of_type(q_requester_alias))
            .join(AccessRequest.requested_group)
            .outerjoin(AccessRequest.resolver.of_type(q_resolver_alias))
            .filter(
                _db.or_(
                    AccessRequest.id.like(f"{q_args.q}%"),
                    cast(AccessRequest.status, String).ilike(like),
                    q_requester_alias.email.ilike(like),
                    q_requester_alias.first_name.ilike(like),
                    q_requester_alias.last_name.ilike(like),
                    q_requester_alias.display_name.ilike(like),
                    (q_requester_alias.first_name + " " + q_requester_alias.last_name).ilike(like),
                    OktaGroup.name.ilike(like),
                    OktaGroup.description.ilike(like),
                    q_resolver_alias.email.ilike(like),
                    q_resolver_alias.first_name.ilike(like),
                    q_resolver_alias.last_name.ilike(like),
                    q_resolver_alias.display_name.ilike(like),
                    (q_resolver_alias.first_name + " " + q_resolver_alias.last_name).ilike(like),
                )
            )
        )
    return paginate(request, query, _adapter, extract=lambda: (q_args.page, q_args.per_page))


@router.get("/{access_request_id}", name="access_request_by_id")
def get_access_request(access_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    ar = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == access_request_id).first()
    if ar is None:
        raise HTTPException(404, "Not Found")
    return dump_orm(_adapter, ar)


@router.post("", name="access_requests_create", status_code=201)
def post_access_request(
    body: CreateAccessRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    requester = (
        db.query(OktaUser)
        .filter(OktaUser.deleted_at.is_(None))
        .filter(OktaUser.id == current_user_id)
        .first()
    )
    if requester is None:
        raise HTTPException(403, "Current user is not allowed to perform this action")
    group = db.query(OktaGroup).filter(OktaGroup.id == body.group_id).filter(OktaGroup.deleted_at.is_(None)).first()
    if group is None:
        raise HTTPException(404, "Group not found")
    if not group.is_managed:
        raise HTTPException(400, "Groups not managed by Access cannot be modified")

    # Auto-cancel any prior PENDING access request from the same user for the
    # same group + ownership mode. Without this, a user clicking "Request"
    # twice produces multiple PENDING rows that approvers see as duplicates.
    existing_pending = (
        db.query(AccessRequest)
        .filter(AccessRequest.status == AccessRequestStatus.PENDING)
        .filter(AccessRequest.requester_user_id == requester.id)
        .filter(AccessRequest.requested_group_id == group.id)
        .filter(AccessRequest.request_ownership.is_(body.group_owner))
        .filter(AccessRequest.resolved_at.is_(None))
        .all()
    )
    for prior in existing_pending:
        RejectAccessRequest(
            access_request=prior,
            current_user_id=current_user_id,
            rejection_reason="Superseded by a newer request from the same user",
            notify_requester=False,
        ).execute()

    ar = CreateAccessRequest(
        requester_user=requester,
        requested_group=group,
        request_ownership=body.group_owner,
        request_reason=body.reason or "",
        request_ending_at=body.ending_at,
    ).execute()
    if ar is None:
        # Belt and suspenders — `is_managed` is the only path that returns
        # None today, but covering the contract guards against drift.
        raise HTTPException(400, "Access request could not be created")
    refreshed = db.query(AccessRequest).options(*_post_load_options()).filter(AccessRequest.id == ar.id).first()
    return dump_orm(_adapter, refreshed)


@router.put("/{access_request_id}", name="access_request_by_id_put")
def put_access_request(
    access_request_id: str,
    body: ResolveAccessRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    from api.auth.permissions import can_manage_group
    from api.operations.constraints import CheckForReason

    ar = (
        db.query(AccessRequest)
        .options(joinedload(AccessRequest.active_requested_group))
        .filter(AccessRequest.id == access_request_id)
        .first()
    )
    if ar is None:
        raise HTTPException(404, "Not Found")

    # Requester can always reject their own request, but cannot approve it.
    if ar.requester_user_id == current_user_id:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not can_manage_group(db, current_user_id, ar.active_requested_group):
        raise HTTPException(403, "Current user is not allowed to perform this action")

    if body.approved:
        valid, err_message = CheckForReason(
            group=ar.active_requested_group,
            reason=body.reason,
            members_to_add=[ar.requester_user_id] if not ar.request_ownership else [],
            owners_to_add=[ar.requester_user_id] if ar.request_ownership else [],
        ).execute_for_group()
        if not valid:
            raise HTTPException(400, err_message)

    if ar.status != AccessRequestStatus.PENDING or ar.resolved_at is not None:
        raise HTTPException(400, "Access request is not pending")

    if body.approved:
        if not ar.requested_group.is_managed:
            raise HTTPException(400, "Groups not managed by Access cannot be modified")
        ApproveAccessRequest(
            access_request=ar,
            approver_user=current_user_id,
            approval_reason=body.reason or "",
            ending_at=body.ending_at,
        ).execute()
    else:
        RejectAccessRequest(
            access_request=ar,
            rejection_reason=body.reason or "",
            notify_requester=ar.requester_user_id != current_user_id,
            current_user_id=current_user_id,
        ).execute()
    refreshed = db.query(AccessRequest).options(*_load_options()).filter(AccessRequest.id == access_request_id).first()
    return dump_orm(_adapter, refreshed)
