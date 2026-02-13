from flask import abort, g, request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import String, cast
from sqlalchemy.orm import aliased, joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationHelpers
from api.extensions import db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    GroupRequest,
    Tag,
)
from api.models.app import get_app_managers
from api.operations import (
    ApproveGroupRequest,
    CreateGroupRequest,
    RejectGroupRequest,
)
from api.pagination import paginate
from api.views.schemas import (
    CreateGroupRequestSchema,
    ResolveGroupRequestSchema,
    GroupRequestPaginationSchema,
    GroupRequestSchema,
    SearchGroupRequestPaginationRequestSchema,
)

# Use selectinload for one-to-many eager loading and used joinedload for one-to-one eager loading
DEFAULT_LOAD_OPTIONS = (
    joinedload(GroupRequest.requester),
    joinedload(GroupRequest.resolver),
)


class GroupRequestResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(GroupRequestSchema)
    def get(self, group_request_id: str) -> ResponseReturnValue:
        schema = GroupRequestSchema(
            only=(
                "id",
                "created_at",
                "updated_at",
                "resolved_at",
                "status",
                "requester.id",
                "requester.email",
                "requester.first_name",
                "requester.last_name",
                "requester.display_name",
                "requester.deleted_at",
                "requested_group_name",
                "requested_group_description",
                "requested_group_type",
                "requested_app_id",
                "requested_group_tags",
                "requested_ownership_ending_at",
                "request_reason",
                "resolver.id",
                "resolver.email",
                "resolver.first_name",
                "resolver.last_name",
                "resolver.display_name",
                "resolved_group_name",
                "resolved_group_description",
                "resolved_group_type",
                "resolved_app_id",
                "resolved_group_tags",
                "resolved_ownership_ending_at",
                "resolution_reason",
                "approved_group_id",
            )
        )
        group_request = (
            GroupRequest.query.options(DEFAULT_LOAD_OPTIONS).filter(GroupRequest.id == group_request_id).first_or_404()
        )
        return schema.dump(group_request)

    @FlaskApiSpecDecorators.request_schema(ResolveGroupRequestSchema)
    @FlaskApiSpecDecorators.response_schema(GroupRequestSchema)
    def put(self, group_request_id: str) -> ResponseReturnValue:
        group_request = (
            GroupRequest.query.options(
                joinedload(GroupRequest.active_requester),
            )
            .filter(GroupRequest.id == group_request_id)
            .first_or_404()
        )

        group_request_args = ResolveGroupRequestSchema().load(request.get_json())

        # Check if the current user is the user who created the request (they can always reject their own requests)
        if group_request.requester_user_id == g.current_user_id:
            if group_request_args["approved"]:
                abort(403, "Users cannot approve their own requests")
        # Check if the current user can approve: admins can approve all, app owners can approve for their apps
        elif not AuthorizationHelpers.is_access_admin(g.current_user_id):
            # If this is an app group request, check if current user is an app owner
            if group_request.resolved_app_id is not None:
                app_owner_approvers = get_app_managers(group_request.resolved_app_id)
                if g.current_user_id not in [approver.id for approver in app_owner_approvers]:
                    abort(403, "Current user is not allowed to perform this action")
            else:
                abort(403, "Current user is not allowed to perform this action")

        if group_request.status != AccessRequestStatus.PENDING or group_request.resolved_at is not None:
            abort(400, "Group request is not pending")

        # Update resolved fields if provided
        if "resolved_group_name" in group_request_args:
            group_request.resolved_group_name = group_request_args["resolved_group_name"]
        if "resolved_group_description" in group_request_args:
            group_request.resolved_group_description = group_request_args["resolved_group_description"]
        if "resolved_group_type" in group_request_args:
            group_request.resolved_group_type = group_request_args["resolved_group_type"]
        if "resolved_app_id" in group_request_args:
            group_request.resolved_app_id = group_request_args["resolved_app_id"]
        if "resolved_group_tags" in group_request_args:
            group_request.resolved_group_tags = group_request_args["resolved_group_tags"]
        if "resolved_ownership_ending_at" in group_request_args:
            group_request.resolved_ownership_ending_at = group_request_args["resolved_ownership_ending_at"]

        db.session.commit()

        if group_request_args["approved"]:
            ApproveGroupRequest(
                group_request=group_request,
                approver_user=g.current_user_id,
                approval_reason=group_request_args.get("resolution_reason", ""),
            ).execute()
        else:
            RejectGroupRequest(
                group_request=group_request,
                rejection_reason=group_request_args.get("resolution_reason", ""),
                notify_requester=group_request.requester_user_id != g.current_user_id,
                current_user_id=g.current_user_id,
            ).execute()

        group_request = GroupRequest.query.options(DEFAULT_LOAD_OPTIONS).filter(GroupRequest.id == group_request.id).first()
        return GroupRequestSchema(
            only=(
                "id",
                "created_at",
                "updated_at",
                "resolved_at",
                "status",
                "requester.id",
                "requester.email",
                "requester.first_name",
                "requester.last_name",
                "requester.display_name",
                "requester.deleted_at",
                "requested_group_name",
                "requested_group_description",
                "requested_group_type",
                "requested_app_id",
                "requested_group_tags",
                "requested_ownership_ending_at",
                "request_reason",
                "resolver.id",
                "resolver.email",
                "resolver.first_name",
                "resolver.last_name",
                "resolver.display_name",
                "resolved_group_name",
                "resolved_group_description",
                "resolved_group_type",
                "resolved_app_id",
                "resolved_group_tags",
                "resolved_ownership_ending_at",
                "resolution_reason",
            )
        ).dump(group_request)


class GroupRequestList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchGroupRequestPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(GroupRequestPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchGroupRequestPaginationRequestSchema().load(request.args)

        query = GroupRequest.query.options(DEFAULT_LOAD_OPTIONS).order_by(GroupRequest.created_at.desc())

        if "status" in search_args:
            query = query.filter(GroupRequest.status == search_args["status"])

        if "requester_user_id" in search_args:
            if search_args["requester_user_id"] == "@me":
                query = query.filter(GroupRequest.requester_user_id == g.current_user_id)
            else:
                requester_alias = aliased(OktaUser)
                query = query.join(GroupRequest.requester.of_type(requester_alias)).filter(
                    db.or_(
                        GroupRequest.requester_user_id == search_args["requester_user_id"],
                        requester_alias.email.ilike(search_args["requester_user_id"]),
                    )
                )

        if "requested_group_type" in search_args:
            query = query.filter(GroupRequest.requested_group_type == search_args["requested_group_type"])

        if "requested_app_id" in search_args:
            query = query.filter(GroupRequest.requested_app_id == search_args["requested_app_id"])

        if "assignee_user_id" in search_args:
            assignee_user_id = search_args["assignee_user_id"]
            if search_args["assignee_user_id"] == "@me":
                assignee_user_id = g.current_user_id

            assignee_user = OktaUser.query.filter(
                db.or_(
                    OktaUser.id == assignee_user_id,
                    OktaUser.email.ilike(assignee_user_id),
                )
            ).first()

            if assignee_user is not None:
                # Admins can be assigned all requests, app owners can be assigned for apps they own
                if AuthorizationHelpers.is_access_admin(assignee_user_id):
                    # Admins can see all pending requests
                    pass
                else:
                    # Non-admins can only see app group requests for apps they own
                    # Get all apps this user owns
                    owned_app_ids = []
                    apps = App.query.filter(App.deleted_at.is_(None)).all()
                    for app in apps:
                        app_managers = get_app_managers(app.id)
                        if assignee_user_id in [manager.id for manager in app_managers]:
                            owned_app_ids.append(app.id)
                    
                    # Filter to only show app group requests for owned apps
                    if len(owned_app_ids) > 0:
                        query = query.filter(
                            db.and_(
                                GroupRequest.requested_app_id.in_(owned_app_ids),
                                GroupRequest.requested_group_type == AppGroup.__mapper_args__["polymorphic_identity"]
                            )
                        )
                    else:
                        # User owns no apps, show nothing
                        query = query.filter(False)
                
                # remove own requests regardless of whether admin
                query = query.filter(GroupRequest.requester_user_id != assignee_user_id)
            else:
                query = query.filter(False)

        if "resolver_user_id" in search_args:
            if search_args["resolver_user_id"] == "@me":
                query = query.filter(GroupRequest.resolver_user_id == g.current_user_id)
            else:
                resolver_alias = aliased(OktaUser)
                query = query.outerjoin(GroupRequest.resolver.of_type(resolver_alias)).filter(
                    db.or_(
                        GroupRequest.resolver_user_id == search_args["resolver_user_id"],
                        resolver_alias.email.ilike(search_args["resolver_user_id"]),
                    )
                )

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            requester_alias = aliased(OktaUser)
            resolver_alias = aliased(OktaUser)
            query = (
                query.join(GroupRequest.requester.of_type(requester_alias))
                .outerjoin(GroupRequest.resolver.of_type(resolver_alias))
                .filter(
                    db.or_(
                        GroupRequest.id.like(f"{search_args['q']}%"),
                        cast(GroupRequest.status, String).ilike(like_search),
                        requester_alias.email.ilike(like_search),
                        requester_alias.first_name.ilike(like_search),
                        requester_alias.last_name.ilike(like_search),
                        requester_alias.display_name.ilike(like_search),
                        (requester_alias.first_name + " " + requester_alias.last_name).ilike(like_search),
                        GroupRequest.requested_group_name.ilike(like_search),
                        GroupRequest.requested_group_description.ilike(like_search),
                        GroupRequest.requested_group_type.ilike(like_search),
                        GroupRequest.resolved_group_name.ilike(like_search),
                        GroupRequest.resolved_group_description.ilike(like_search),
                        GroupRequest.resolved_group_type.ilike(like_search),
                        resolver_alias.email.ilike(like_search),
                        resolver_alias.first_name.ilike(like_search),
                        resolver_alias.last_name.ilike(like_search),
                        resolver_alias.display_name.ilike(like_search),
                        (resolver_alias.first_name + " " + resolver_alias.last_name).ilike(like_search),
                    )
                )
            )

        return paginate(
            query,
            GroupRequestSchema(
                many=True,
                only=(
                    "id",
                    "created_at",
                    "updated_at",
                    "resolved_at",
                    "status",
                    "requester.id",
                    "requester.email",
                    "requester.first_name",
                    "requester.last_name",
                    "requester.display_name",
                    "requester.deleted_at",
                    "requested_group_name",
                    "requested_group_type",
                    "requested_app_id",
                    "resolver.id",
                    "resolver.email",
                    "resolver.first_name",
                    "resolver.last_name",
                    "resolver.display_name",
                ),
            ),
        )

    @FlaskApiSpecDecorators.request_schema(CreateGroupRequestSchema)
    @FlaskApiSpecDecorators.response_schema(GroupRequestSchema)
    def post(self) -> ResponseReturnValue:
        group_request_args = CreateGroupRequestSchema().load(request.get_json())

        # Ensure requester not deleted
        if OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(
            OktaUser.id == g.current_user_id
        ).first() is None:
            abort(403, "Current user is not allowed to perform this action")

        # Validate app exists if creating an app group
        if group_request_args.get("requested_group_type") == AppGroup.__mapper_args__["polymorphic_identity"]:
            if "requested_app_id" not in group_request_args or group_request_args["requested_app_id"] is None:
                abort(400, "app_id is required for app group requests")
            
            app = (
                App.query.filter(App.deleted_at.is_(None))
                .filter(App.id == group_request_args["requested_app_id"])
                .first()
            )
            if app is None:
                abort(404, "App not found")
        
        # Validate tags exist
        if "requested_group_tags" in group_request_args and group_request_args["requested_group_tags"]:
            tags = Tag.query.filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(group_request_args["requested_group_tags"])).all()
            if len(tags) != len(group_request_args["requested_group_tags"]):
                abort(400, "One or more tags not found")

        # Close any existing pending group requests with the same name (and app if app group)
        existing_group_requests_query = (
            GroupRequest.query.filter(GroupRequest.requested_group_name == group_request_args["requested_group_name"])
            .filter(GroupRequest.status == AccessRequestStatus.PENDING)
            .filter(GroupRequest.resolved_at.is_(None))
        )
        
        # For app groups, also match on app_id to ensure we're only closing true duplicates
        if group_request_args.get("requested_group_type") == AppGroup.__mapper_args__["polymorphic_identity"]:
            existing_group_requests_query = existing_group_requests_query.filter(
                GroupRequest.requested_app_id == group_request_args.get("requested_app_id")
            )
        
        existing_group_requests = existing_group_requests_query.all()
        
        for existing_group_request in existing_group_requests:
            RejectGroupRequest(
                group_request=existing_group_request,
                rejection_reason="Closed due to duplicate group request creation",
                notify_requester=False,
                current_user_id=g.current_user_id,
            ).execute()

        group_request = CreateGroupRequest(
            requester_user=g.current_user_id,
            requested_group_name=group_request_args["requested_group_name"],
            requested_group_description=group_request_args.get("requested_group_description", ""),
            requested_group_type=group_request_args["requested_group_type"],
            requested_app_id=group_request_args.get("requested_app_id"),
            requested_group_tags=group_request_args.get("requested_group_tags", []),
            requested_ownership_ending_at=group_request_args.get("requested_ownership_ending_at"),
            request_reason=group_request_args.get("request_reason", ""),
        ).execute()

        if group_request is None:
            abort(400, "Failed to create group request")

        group_request = GroupRequest.query.options(DEFAULT_LOAD_OPTIONS).filter(GroupRequest.id == group_request.id).first()
        return (
            GroupRequestSchema(
                only=(
                    "id",
                    "created_at",
                    "updated_at",
                    "resolved_at",
                    "status",
                    "requester.id",
                    "requester.email",
                    "requester.first_name",
                    "requester.last_name",
                    "requester.display_name",
                    "requester.deleted_at",
                    "requested_group_name",
                    "requested_group_description",
                    "requested_group_type",
                    "requested_app_id",
                    "requested_group_tags",
                    "requested_ownership_ending_at",
                    "request_reason",
                ),
            ).dump(group_request),
            201,
        )
