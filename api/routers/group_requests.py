"""Group requests router."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy import String, and_, cast, false, func, or_, select
from sqlalchemy.orm import aliased, joinedload
from starlette.requests import Request

from api.auth.dependencies import CurrentRequester, CurrentUserId
from api.database import DbSession
from api.models import AccessRequestStatus, App, GroupRequest, OktaUser, Tag
from api.operations import ApproveGroupRequest, CreateGroupRequest, RejectGroupRequest
from api.operations._ending_at import USE_REQUESTED
from api.pagination import Page, validated
from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginFilteringError,
    raise_http_for_plugin_filtering_error,
    validate_group_plugin_config_or_raise,
)
from api.routers._eager import service_principal_options
from api.routers._fan_out import defer_fan_out
from api.routers._named_principals import attach_named_principals
from api.routers._requests import filter_by_requester, join_requester_for_search, withdraw_prior_requests
from api.schemas import (
    CreateGroupRequestBody,
    GroupRequestDetail,
    ResolveGroupRequestBody,
    SearchGroupRequestQuery,
)
from api.schemas.requests_schemas import _AppGroupRequestBody

router = APIRouter(prefix="/api/group-requests", tags=["group-requests"], dependencies=[Depends(defer_fan_out)])


def _load_options() -> tuple:
    # `GroupRequestDetail` — the response schema for every route below (list
    # included) — serializes only `requester` / `resolver` and the
    # `approved_group_id` *column*. It exposes neither `active_requester`,
    # `active_resolver`, nor the `approved_group` relationship, and no handler
    # reads them (the approve/resolve operations re-query their own graph), so
    # eager-loading them just adds dead JOINs to every row of every page.
    return (
        joinedload(GroupRequest.requester),
        joinedload(GroupRequest.requester_service_principal).options(*service_principal_options()),
        joinedload(GroupRequest.resolver_service_principal).options(*service_principal_options()),
        joinedload(GroupRequest.resolver),
    )


_NAMED_USER_FIELDS = (
    "requested_owner_user_ids",
    "requested_member_user_ids",
    "resolved_owner_user_ids",
    "resolved_member_user_ids",
)
_NAMED_ROLE_FIELDS = (
    "requested_owner_role_ids",
    "requested_member_role_ids",
    "resolved_owner_role_ids",
    "resolved_member_role_ids",
)


async def _attach_named_principals(db: DbSession, details: Sequence[GroupRequestDetail]) -> None:
    await attach_named_principals(
        db,
        details,
        lambda d: (uid for f in _NAMED_USER_FIELDS for uid in getattr(d, f)),
        lambda d: (rid for f in _NAMED_ROLE_FIELDS for rid in getattr(d, f)),
    )


async def _detail(db: DbSession, gr: GroupRequest) -> GroupRequestDetail:
    detail = GroupRequestDetail.model_validate(gr, from_attributes=True)
    await _attach_named_principals(db, [detail])
    return detail


@router.get("", name="group_requests")
async def list_group_requests(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchGroupRequestQuery, Query()],
) -> Page[GroupRequestDetail]:
    from api.auth.permissions import is_access_admin
    from api.models.app_group import get_app_managers

    stmt = select(GroupRequest).options(*_load_options()).order_by(GroupRequest.created_at.desc())

    if q_args.status:
        stmt = stmt.where(GroupRequest.status == q_args.status)

    if q_args.requester_user_id:
        stmt = filter_by_requester(stmt, GroupRequest, q_args.requester_user_id, current_user_id)

    if q_args.requested_group_type:
        stmt = stmt.where(GroupRequest.requested_group_type == q_args.requested_group_type)

    if q_args.requested_app_id:
        stmt = stmt.where(GroupRequest.requested_app_id == q_args.requested_app_id)

    if q_args.assignee_user_id:
        # "Requests I can resolve". Admins see every pending request; app
        # owners see app-group requests for apps they own. In both cases the
        # assignee's own requests are stripped out.
        assignee_user_id = current_user_id if q_args.assignee_user_id == "@me" else q_args.assignee_user_id
        assignee_user = (
            await db.scalars(
                select(OktaUser).where(or_(OktaUser.id == assignee_user_id, OktaUser.email.ilike(assignee_user_id)))
            )
        ).first()
        if assignee_user is not None:
            if not await is_access_admin(db, assignee_user.id):
                owned_app_ids: list[str] = []
                for app in (await db.scalars(select(App).where(App.deleted_at.is_(None)))).all():
                    manager_ids = [m.id for m in await get_app_managers(app.id)]
                    if assignee_user.id in manager_ids:
                        owned_app_ids.append(app.id)
                if owned_app_ids:
                    stmt = stmt.where(
                        and_(
                            GroupRequest.requested_app_id.in_(owned_app_ids),
                            GroupRequest.requested_group_type == "app_group",
                        )
                    )
                else:
                    stmt = stmt.where(false())
            # A NULL user requester (a service principal's request) is never the assignee's own.
            stmt = stmt.where(
                or_(GroupRequest.requester_user_id.is_(None), GroupRequest.requester_user_id != assignee_user.id)
            )
        else:
            stmt = stmt.where(false())

    if q_args.resolver_user_id:
        if q_args.resolver_user_id == "@me":
            stmt = stmt.where(GroupRequest.resolver_user_id == current_user_id)
        else:
            resolver_alias = aliased(OktaUser)
            stmt = stmt.outerjoin(GroupRequest.resolver.of_type(resolver_alias)).where(
                or_(
                    GroupRequest.resolver_user_id == q_args.resolver_user_id,
                    resolver_alias.email.ilike(q_args.resolver_user_id),
                )
            )

    # Free-text search over id prefix, status, requester / resolver
    # name+email, requested + resolved group name/description/type.
    if q_args.q:
        like = f"%{q_args.q}%"
        q_resolver_alias = aliased(OktaUser)
        stmt, requester_clauses = join_requester_for_search(stmt, GroupRequest, like)
        stmt = stmt.outerjoin(GroupRequest.resolver.of_type(q_resolver_alias)).where(
            or_(
                GroupRequest.id.like(f"{q_args.q}%"),
                cast(GroupRequest.status, String).ilike(like),
                *requester_clauses,
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

    page = await apaginate(db, stmt, transformer=validated(GroupRequestDetail))
    await _attach_named_principals(db, page.items)
    return page


@router.get("/{group_request_id}", name="group_request_by_id")
async def get_group_request(group_request_id: str, db: DbSession, current_user_id: CurrentUserId) -> GroupRequestDetail:
    gr = (
        await db.scalars(select(GroupRequest).options(*_load_options()).where(GroupRequest.id == group_request_id))
    ).first()
    if gr is None:
        raise HTTPException(404, "Not Found")
    return await _detail(db, gr)


@router.post("", name="group_requests_create", status_code=201)
async def post_group_request(
    body: CreateGroupRequestBody,
    db: DbSession,
    requester: CurrentRequester,
) -> GroupRequestDetail:
    current_user_id = requester.id

    requested_app_id = body.requested_app_id if isinstance(body, _AppGroupRequestBody) else None

    # App-group requests must point at a real, non-deleted app.
    if body.requested_group_type == "app_group":
        if requested_app_id is None:
            raise HTTPException(400, "app_id is required for app group requests")
        app = (await db.scalars(select(App).where(App.deleted_at.is_(None)).where(App.id == requested_app_id))).first()
        if app is None:
            raise HTTPException(404, "App not found")

        # Validate any supplied plugin group config against the app's lifecycle plugin,
        # through the same helper the group create/update endpoints use. `old_plugin_data`
        # is omitted deliberately: a request describes a not-yet-created group, so there
        # is no prior config and immutable fields stay freely settable.
        validate_group_plugin_config_or_raise(
            body.requested_plugin_data,
            app.plugin_data or {},
            app.app_group_lifecycle_plugin,
        )

    # Every requested tag id must resolve to a non-deleted tag.
    if body.requested_group_tags:
        tags = (
            await db.scalars(select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.id.in_(body.requested_group_tags)))
        ).all()
        if len(tags) != len(body.requested_group_tags):
            raise HTTPException(400, "One or more tags not found")

    # A retry replaces the requester's pending request for the same group name
    # (and app, for an app group), so clicking "Request" twice or a service
    # retrying doesn't leave several pending.
    same_target = [func.lower(GroupRequest.requested_group_name) == func.lower(body.requested_group_name)]
    if body.requested_group_type == "app_group":
        same_target.append(GroupRequest.requested_app_id == requested_app_id)
    await withdraw_prior_requests(
        db,
        GroupRequest,
        requester,
        same_target,
        lambda prior: RejectGroupRequest(
            group_request=prior,
            rejection_reason="Closed due to duplicate group request creation",
            notify_requester=False,
            current_user_id=current_user_id,
        ).execute(),
    )

    # execute() may auto-approve (app-owner self-approval / conditional access), which
    # re-validates plugin config; a ValueError is bad input, while a filtering error
    # splits 400/500 by whether the plugin id is merely unknown. Same handling as the
    # PUT/approve path below.
    try:
        gr = await CreateGroupRequest(
            requester_user=requester,
            requested_group_name=body.requested_group_name,
            requested_group_description=body.requested_group_description or "",
            requested_group_type=body.requested_group_type,
            requested_app_id=requested_app_id,
            requested_group_tags=body.requested_group_tags,
            requested_ownership_ending_at=body.requested_ownership_ending_at,
            request_reason=body.request_reason or "",
            requested_plugin_data=body.requested_plugin_data if isinstance(body, _AppGroupRequestBody) else {},
            requested_owner_user_ids=body.requested_owner_user_ids,
            requested_owner_role_ids=body.requested_owner_role_ids,
            requested_member_user_ids=body.requested_member_user_ids,
            requested_member_role_ids=body.requested_member_role_ids,
            requested_membership_ending_at=body.requested_membership_ending_at,
        ).execute()
    except ValueError as e:
        raise HTTPException(400, f"Failed to create group request: {e}") from e
    except AppGroupLifecyclePluginFilteringError as e:
        raise_http_for_plugin_filtering_error(e)
    if gr is None:
        raise HTTPException(400, "Failed to create group request")
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    gr_id = gr.id
    db.expire_all()
    refreshed = (
        await db.scalars(select(GroupRequest).options(*_load_options()).where(GroupRequest.id == gr_id))
    ).first()
    assert refreshed is not None
    return await _detail(db, refreshed)


@router.put("/{group_request_id}", name="group_request_by_id_put")
async def put_group_request(
    group_request_id: str,
    body: ResolveGroupRequestBody,
    db: DbSession,
    current_user_id: CurrentUserId,
) -> GroupRequestDetail:
    from api.auth.permissions import is_access_admin
    from api.models.app_group import get_app_managers

    gr = (
        await db.scalars(select(GroupRequest).options(*_load_options()).where(GroupRequest.id == group_request_id))
    ).first()
    if gr is None:
        raise HTTPException(404, "Not Found")

    # Authorization: requester can always reject their own; otherwise admin
    # or app-owner-of-the-target-app.
    is_requester = gr.is_requested_by(current_user_id)
    if is_requester:
        if body.approved:
            raise HTTPException(403, "Users cannot approve their own requests")
    elif not await is_access_admin(db, current_user_id):
        if gr.requested_app_id is not None:
            approver_ids = [u.id for u in await get_app_managers(gr.requested_app_id)]
            if current_user_id not in approver_ids:
                raise HTTPException(403, "Current user is not allowed to perform this action")
        else:
            raise HTTPException(403, "Current user is not allowed to perform this action")

    if gr.status != AccessRequestStatus.PENDING or gr.resolved_at is not None:
        raise HTTPException(409, "Group request is not pending")

    if body.approved and not await is_access_admin(db, current_user_id):
        type_changed = body.resolved_group_type is not None and body.resolved_group_type != gr.requested_group_type
        app_changed = body.resolved_app_id is not None and body.resolved_app_id != gr.requested_app_id
        if type_changed or app_changed:
            raise HTTPException(
                403,
                "Only admins can change the resolved group type or target app on approval",
            )

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
    if body.resolved_plugin_data is not None:
        gr.resolved_plugin_data = body.resolved_plugin_data

    await db.commit()

    resolution_reason = body.reason or ""
    if body.approved:
        try:
            await ApproveGroupRequest(
                group_request=gr,
                approver_user=current_user_id,
                approval_reason=resolution_reason,
                # A sent end date (null meaning indefinite) replaces the requested one.
                approver_ownership_ending_at=(
                    body.resolved_ownership_ending_at
                    if "resolved_ownership_ending_at" in body.model_fields_set
                    else USE_REQUESTED
                ),
                approver_membership_ending_at=(
                    body.resolved_membership_ending_at
                    if "resolved_membership_ending_at" in body.model_fields_set
                    else USE_REQUESTED
                ),
                owner_user_ids=body.resolved_owner_user_ids,
                owner_role_ids=body.resolved_owner_role_ids,
                member_user_ids=body.resolved_member_user_ids,
                member_role_ids=body.resolved_member_role_ids,
            ).execute()
        # Approval re-validates the resolved plugin config: a ValueError means the
        # config no longer passes, while a filtering error means the app's plugin id is
        # unknown (400) or its plugin is misconfigured (500).
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except AppGroupLifecyclePluginFilteringError as e:
            raise_http_for_plugin_filtering_error(e)
    else:
        await RejectGroupRequest(
            group_request=gr,
            current_user_id=current_user_id,
            rejection_reason=resolution_reason,
            notify_requester=not is_requester,
        ).execute()
    # Drop cached ORM state so the response reflects what the operation
    # committed (expire_on_commit=False keeps pre-operation state otherwise).
    db.expire_all()
    refreshed = (
        await db.scalars(select(GroupRequest).options(*_load_options()).where(GroupRequest.id == group_request_id))
    ).first()
    assert refreshed is not None
    return await _detail(db, refreshed)
