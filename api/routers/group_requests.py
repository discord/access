"""Group requests router."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import TypeAdapter
from sqlalchemy import String, cast
from sqlalchemy.orm import aliased, joinedload
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.extensions import db as _db
from api.models import AccessRequestStatus, App, GroupRequest, OktaUser, Tag
from api.operations import ApproveGroupRequest, CreateGroupRequest, RejectGroupRequest
from api.pagination import paginate
from api.schemas import (
    CreateGroupRequestBody,
    GroupRequestDetail,
    ResolveGroupRequestBody,
    SearchGroupRequestPaginationQuery,
)
from api.schemas._serialize import dump_orm
from api.schemas.requests_schemas import _AppGroupRequestBody

router = APIRouter(prefix="/api/group-requests", tags=["group-requests"])
_adapter = TypeAdapter(GroupRequestDetail)


def _load_options() -> tuple:
    return (
        joinedload(GroupRequest.requester),
        joinedload(GroupRequest.active_requester),
        joinedload(GroupRequest.resolver),
        joinedload(GroupRequest.active_resolver),
        joinedload(GroupRequest.approved_group),
    )


@router.get("", name="group_requests")
def list_group_requests(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchGroupRequestPaginationQuery, Query()],
) -> dict[str, Any]:
    from api.auth.permissions import is_access_admin
    from api.models.app_group import get_app_managers

    query = db.query(GroupRequest).options(*_load_options()).order_by(GroupRequest.created_at.desc())

    if q_args.status:
        query = query.filter(GroupRequest.status == q_args.status)

    if q_args.requester_user_id:
        if q_args.requester_user_id == "@me":
            query = query.filter(GroupRequest.requester_user_id == current_user_id)
        else:
            requester_alias = aliased(OktaUser)
            query = query.join(GroupRequest.requester.of_type(requester_alias)).filter(
                _db.or_(
                    GroupRequest.requester_user_id == q_args.requester_user_id,
                    requester_alias.email.ilike(q_args.requester_user_id),
                )
            )

    if q_args.requested_group_type:
        query = query.filter(GroupRequest.requested_group_type == q_args.requested_group_type)

    if q_args.requested_app_id:
        query = query.filter(GroupRequest.requested_app_id == q_args.requested_app_id)

    if q_args.assignee_user_id:
        # "Requests I can resolve". Admins see every pending request; app
        # owners see app-group requests for apps they own. In both cases the
        # assignee's own requests are stripped out.
        assignee_user_id = current_user_id if q_args.assignee_user_id == "@me" else q_args.assignee_user_id
        assignee_user = (
            db.query(OktaUser)
            .filter(_db.or_(OktaUser.id == assignee_user_id, OktaUser.email.ilike(assignee_user_id)))
            .first()
        )
        if assignee_user is not None:
            if not is_access_admin(db, assignee_user.id):
                owned_app_ids: list[str] = []
                for app in db.query(App).filter(App.deleted_at.is_(None)).all():
                    manager_ids = [m.id for m in get_app_managers(app.id)]
                    if assignee_user.id in manager_ids:
                        owned_app_ids.append(app.id)
                if owned_app_ids:
                    query = query.filter(
                        _db.and_(
                            GroupRequest.requested_app_id.in_(owned_app_ids),
                            GroupRequest.requested_group_type == "app_group",
                        )
                    )
                else:
                    query = query.filter(False)
            query = query.filter(GroupRequest.requester_user_id != assignee_user.id)
        else:
            query = query.filter(False)

    if q_args.resolver_user_id:
        if q_args.resolver_user_id == "@me":
            query = query.filter(GroupRequest.resolver_user_id == current_user_id)
        else:
            resolver_alias = aliased(OktaUser)
            query = query.outerjoin(GroupRequest.resolver.of_type(resolver_alias)).filter(
                _db.or_(
                    GroupRequest.resolver_user_id == q_args.resolver_user_id,
                    resolver_alias.email.ilike(q_args.resolver_user_id),
                )
            )

    # Free-text search over id prefix, status, requester / resolver
    # name+email, requested + resolved group name/description/type.
    if q_args.q:
        like = f"%{q_args.q}%"
        q_requester_alias = aliased(OktaUser)
        q_resolver_alias = aliased(OktaUser)
        query = (
            query.join(GroupRequest.requester.of_type(q_requester_alias))
            .outerjoin(GroupRequest.resolver.of_type(q_resolver_alias))
            .filter(
                _db.or_(
                    GroupRequest.id.like(f"{q_args.q}%"),
                    cast(GroupRequest.status, String).ilike(like),
                    q_requester_alias.email.ilike(like),
                    q_requester_alias.first_name.ilike(like),
                    q_requester_alias.last_name.ilike(like),
                    q_requester_alias.display_name.ilike(like),
                    (q_requester_alias.first_name + " " + q_requester_alias.last_name).ilike(like),
                    GroupRequest.requested_group_name.ilike(like),
                    GroupRequest.requested_group_description.ilike(like),
                    GroupRequest.requested_group_type.ilike(like),
                    GroupRequest.resolved_group_name.ilike(like),
                    GroupRequest.resolved_group_description.ilike(like),
                    GroupRequest.resolved_group_type.ilike(like),
                    q_resolver_alias.email.ilike(like),
                    q_resolver_alias.first_name.ilike(like),
                    q_resolver_alias.last_name.ilike(like),
                    q_resolver_alias.display_name.ilike(like),
                    (q_resolver_alias.first_name + " " + q_resolver_alias.last_name).ilike(like),
                )
            )
        )

    return paginate(request, query, _adapter, extract=lambda: (q_args.page, q_args.per_page))


@router.get("/{group_request_id}", name="group_request_by_id")
def get_group_request(group_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    gr = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    return dump_orm(_adapter, gr)


@router.post("", name="group_requests_create", status_code=201)
def post_group_request(
    body: CreateGroupRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    # Soft-deleted requesters cannot create new requests; Flask returned 403
    # here, not 404.
    requester = (
        db.query(OktaUser)
        .filter(OktaUser.deleted_at.is_(None))
        .filter(OktaUser.id == current_user_id)
        .first()
    )
    if requester is None:
        raise HTTPException(403, "Current user is not allowed to perform this action")

    requested_app_id = body.requested_app_id if isinstance(body, _AppGroupRequestBody) else None

    # App-group requests must point at a real, non-deleted app.
    if body.requested_group_type == "app_group":
        if requested_app_id is None:
            raise HTTPException(400, "app_id is required for app group requests")
        app = (
            db.query(App)
            .filter(App.deleted_at.is_(None))
            .filter(App.id == requested_app_id)
            .first()
        )
        if app is None:
            raise HTTPException(404, "App not found")

    # Every requested tag id must resolve to a non-deleted tag.
    if body.requested_group_tags:
        tags = (
            db.query(Tag)
            .filter(Tag.deleted_at.is_(None))
            .filter(Tag.id.in_(body.requested_group_tags))
            .all()
        )
        if len(tags) != len(body.requested_group_tags):
            raise HTTPException(400, "One or more tags not found")

    # Auto-cancel any prior PENDING request from the same user for the same
    # group name (and same app, for app-group requests). Without this a user
    # clicking "Request" twice produces multiple PENDING rows.
    existing_query = (
        db.query(GroupRequest)
        .filter(GroupRequest.requested_group_name == body.requested_group_name)
        .filter(GroupRequest.requester_user_id == current_user_id)
        .filter(GroupRequest.status == AccessRequestStatus.PENDING)
        .filter(GroupRequest.resolved_at.is_(None))
    )
    if body.requested_group_type == "app_group":
        existing_query = existing_query.filter(GroupRequest.requested_app_id == requested_app_id)
    for prior in existing_query.all():
        RejectGroupRequest(
            group_request=prior,
            rejection_reason="Closed due to duplicate group request creation",
            notify_requester=False,
            current_user_id=current_user_id,
        ).execute()

    gr = CreateGroupRequest(
        requester_user=requester,
        requested_group_name=body.requested_group_name,
        requested_group_description=body.requested_group_description or "",
        requested_group_type=body.requested_group_type,
        requested_app_id=requested_app_id,
        requested_group_tags=body.requested_group_tags,
        requested_ownership_ending_at=body.requested_ownership_ending_at,
        request_reason=body.request_reason or "",
    ).execute()
    if gr is None:
        raise HTTPException(400, "Failed to create group request")
    refreshed = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == gr.id).first()
    return dump_orm(_adapter, refreshed)


@router.put("/{group_request_id}", name="group_request_by_id_put")
def put_group_request(
    group_request_id: str,
    body: ResolveGroupRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> dict[str, Any]:
    from api.auth.permissions import is_access_admin
    from api.models.app_group import get_app_managers

    gr = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    if gr is None:
        raise HTTPException(404, "Not Found")

    # Authorization: requester can always reject their own; otherwise admin
    # or app-owner-of-the-target-app.
    if gr.requester_user_id == current_user_id:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not is_access_admin(db, current_user_id):
        if gr.requested_app_id is not None:
            approver_ids = [u.id for u in get_app_managers(gr.requested_app_id)]
            if current_user_id not in approver_ids:
                raise HTTPException(403, "Current user is not allowed to perform this action")
        else:
            raise HTTPException(403, "Current user is not allowed to perform this action")

    if gr.status != AccessRequestStatus.PENDING or gr.resolved_at is not None:
        raise HTTPException(400, "Group request is not pending")

    # Update resolved_* fields if the body carried them.
    if body.resolved_group_name is not None:
        gr.resolved_group_name = body.resolved_group_name
    if body.resolved_group_description is not None:
        gr.resolved_group_description = body.resolved_group_description
    if body.resolved_group_type is not None:
        gr.resolved_group_type = body.resolved_group_type
    if body.resolved_app_id is not None:
        gr.resolved_app_id = body.resolved_app_id
    if body.resolved_group_tags is not None:
        gr.resolved_group_tags = body.resolved_group_tags
    if body.resolved_ownership_ending_at is not None:
        gr.resolved_ownership_ending_at = body.resolved_ownership_ending_at

    db.commit()

    resolution_reason = body.resolution_reason or body.reason or ""
    if body.approved:
        ApproveGroupRequest(
            group_request=gr,
            approver_user=current_user_id,
            approval_reason=resolution_reason,
        ).execute()
    else:
        RejectGroupRequest(
            group_request=gr,
            current_user_id=current_user_id,
            rejection_reason=resolution_reason,
            notify_requester=gr.requester_user_id != current_user_id,
        ).execute()
    refreshed = db.query(GroupRequest).options(*_load_options()).filter(GroupRequest.id == group_request_id).first()
    return dump_orm(_adapter, refreshed)
