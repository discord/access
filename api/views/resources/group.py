from flask import abort, current_app, g, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import (
    joinedload,
    selectin_polymorphic,
    selectinload,
    with_polymorphic,
)

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationDecorator, AuthorizationHelpers
from api.extensions import db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import (
    CreateGroup,
    DeleteGroup,
    ModifyGroupTags,
    ModifyGroupType,
    ModifyGroupUsers,
)
from api.operations.constraints import CheckForReason, CheckForSelfAdd
from api.pagination import paginate
from api.services import okta
from api.views.schemas import (
    AuditLogSchema,
    DeleteMessageSchema,
    EventType,
    GroupMemberSchema,
    GroupPaginationSchema,
    PolymorphicGroupSchema,
    SearchGroupPaginationRequestSchema,
)

# Use selectinload for one-to-many eager loading and used joinedload for one-to-one eager loading
ROLE_ASSOCIATED_GROUP_TYPES = with_polymorphic(
    OktaGroup,
    [
        AppGroup,
    ],
)
DEFAULT_LOAD_OPTIONS = (
    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
    selectinload(OktaGroup.active_user_memberships).options(
        joinedload(OktaUserGroupMember.active_user),
        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
    ),
    selectinload(OktaGroup.active_user_ownerships).options(
        joinedload(OktaUserGroupMember.active_user),
        joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(RoleGroupMap.active_role_group),
    ),
    selectinload(OktaGroup.active_role_member_mappings).joinedload(RoleGroupMap.active_role_group),
    selectinload(OktaGroup.active_role_owner_mappings).joinedload(RoleGroupMap.active_role_group),
    selectinload(RoleGroup.active_role_associated_group_member_mappings).options(
        joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
            selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                joinedload(OktaGroupTagMap.active_tag), joinedload(OktaGroupTagMap.active_app_tag_mapping)
            ),
            joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
        ),
    ),
    selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(
        joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
            selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                joinedload(OktaGroupTagMap.active_tag), joinedload(OktaGroupTagMap.active_app_tag_mapping)
            ),
            joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
        ),
    ),
    joinedload(AppGroup.app),
    selectinload(OktaGroup.active_group_tags).options(
        joinedload(OktaGroupTagMap.active_app_tag_mapping), joinedload(OktaGroupTagMap.active_tag)
    ),
)

DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS = (
    "all_user_memberships_and_ownerships",
    "active_user_memberships_and_ownerships",
    "active_non_role_user_memberships",
    "active_non_role_user_ownerships",
    "all_role_mappings",
    "active_role_mappings",
    "all_role_associated_group_mappings",
    "active_role_associated_group_mappings",
    "all_group_tags",
)


class GroupResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(PolymorphicGroupSchema)
    def get(self, group_id: str) -> ResponseReturnValue:
        group = (
            db.session.query(OktaGroup)
            .options(DEFAULT_LOAD_OPTIONS)
            .filter(db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
            .first_or_404()
        )

        schema = PolymorphicGroupSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)

        return schema.dump(group)

    # For group type and attributes like name or description
    @AuthorizationDecorator.require_app_or_group_owner_or_access_admin_for_group
    @FlaskApiSpecDecorators.request_schema(PolymorphicGroupSchema)
    @FlaskApiSpecDecorators.response_schema(PolymorphicGroupSchema)
    def put(self, group: OktaGroup) -> ResponseReturnValue:
        schema = PolymorphicGroupSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)
        group_changes = schema.load(request.json)
        old_group_name = group.name

        if not group.is_managed:
            abort(
                400,
                "Groups not managed by Access cannot be modified",
            )

        json_data = request.get_json()
        if "tags_to_remove" in json_data:
            if len(json_data["tags_to_remove"]) > 0 and not AuthorizationHelpers.is_access_admin():
                abort(
                    403,
                    "Current user is not an Access Admin and not allowed to remove tags from this group",
                )

        # Do not allow non-tag modifications of app owner groups (including Access app owner group)
        if type(group) is AppGroup and group.is_owner:
            if len(json_data.get("tags_to_add", [])) > 0 or len(json_data.get("tags_to_remove", [])) > 0:
                ModifyGroupTags(
                    group=group,
                    tags_to_add=json_data.get("tags_to_add", []),
                    tags_to_remove=json_data.get("tags_to_remove", []),
                    current_user_id=g.current_user_id,
                ).execute()
                group = (
                    db.session.query(OktaGroup)
                    .options(DEFAULT_LOAD_OPTIONS)
                    .filter(OktaGroup.deleted_at.is_(None))
                    .filter(OktaGroup.id == group.id)
                    .first()
                )
                return schema.dump(group)
            else:
                abort(
                    400,
                    "Only tags can be modifed for application owner groups",
                )

        # Do not allow non-deleted groups with the same name (case-insensitive)
        if group.name.lower() != group_changes.name.lower():
            existing_group = (
                db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(func.lower(OktaGroup.name) == func.lower(group_changes.name))
                .filter(OktaGroup.deleted_at.is_(None))
                .first()
            )
            if existing_group is not None:
                abort(400, "Group already exists with the same name")

        # Update group type if it's being modified
        if type(group) is not type(group_changes):
            # Only access admins should be able to change group types
            if not AuthorizationHelpers.is_access_admin():
                abort(
                    403,
                    "Current user is not an Access admin and not allowed to change group types",
                )
            group = ModifyGroupType(
                group=group, group_changes=group_changes, current_user_id=g.current_user_id
            ).execute()

        # Update additional fields like name, description, etc.
        group = schema.load(request.json, instance=group)
        okta.update_group(group.id, group.name, group.description)
        db.session.commit()

        ModifyGroupTags(
            group=group,
            tags_to_add=json_data.get("tags_to_add", []),
            tags_to_remove=json_data.get("tags_to_remove", []),
            current_user_id=g.current_user_id,
        ).execute()

        group = (
            db.session.query(OktaGroup)
            .options(DEFAULT_LOAD_OPTIONS)
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == group.id)
            .first()
        )

        # Audit logging gnly log if group name changed
        if old_group_name.lower() != group.name.lower():
            current_app.logger.info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.group_modify_name,
                        "user_agent": request.headers.get("User-Agent"),
                        "ip": request.headers.get(
                            "X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr)
                        ),
                        "current_user_id": g.current_user_id,
                        "current_user_email": getattr(db.session.get(OktaUser, g.current_user_id), "email", None),
                        "group": group,
                        "old_group_name": old_group_name,
                    }
                )
            )

        return schema.dump(group)

    # For soft-deleting the group and all associated memberships
    @AuthorizationDecorator.require_app_or_group_owner_or_access_admin_for_group
    @FlaskApiSpecDecorators.response_schema(DeleteMessageSchema)
    def delete(self, group: OktaGroup) -> ResponseReturnValue:
        if not group.is_managed:
            abort(
                400,
                "Groups not managed by Access cannot be modified",
            )
        # Do not allow deletion of app owner groups (including the Access app owner group)
        if type(group) is AppGroup and group.is_owner:
            abort(
                400,
                "Application owner groups cannot be deleted without first deleting the application",
            )

        DeleteGroup(group=group, current_user_id=g.current_user_id).execute()

        return DeleteMessageSchema().dump({"deleted": True})


class GroupAuditResource(MethodResource):
    def get(self, group_id: str) -> ResponseReturnValue:
        return redirect(
            url_for(
                "api-audit.users_and_groups",
                _anchor=None,
                _method=None,
                _scheme=None,
                _external=None,  # To pass type checking
                group_id=group_id,
                **request.args,
            )
        )


class GroupMemberResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(GroupMemberSchema)
    def get(self, group_id: str) -> ResponseReturnValue:
        # Check to make sure this group exists first
        group = (
            OktaGroup.query.filter(OktaGroup.deleted_at.is_(None))
            .filter(db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
            .first_or_404()
        )

        schema = GroupMemberSchema()
        members = (
            OktaUserGroupMember.query.join(OktaUserGroupMember.active_group)
            .options(joinedload(OktaUserGroupMember.active_group))
            .with_entities(OktaUserGroupMember.user_id)
            .filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaUserGroupMember.group_id == group.id)
            .group_by(OktaUserGroupMember.user_id)
        )
        return schema.dump(
            {
                "members": [m.user_id for m in members.filter(OktaUserGroupMember.is_owner.is_(False)).all()],
                "owners": [m.user_id for m in members.filter(OktaUserGroupMember.is_owner.is_(True)).all()],
            }
        )

    @FlaskApiSpecDecorators.request_schema(GroupMemberSchema)
    @FlaskApiSpecDecorators.response_schema(GroupMemberSchema)
    def put(self, group_id: str) -> ResponseReturnValue:
        group = (
            db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
            .first_or_404()
        )

        schema = GroupMemberSchema()
        user_changes = schema.load(request.get_json())

        if not group.is_managed:
            abort(
                400,
                "Groups not managed by Access cannot be modified",
            )

        # Check if the current user can manage this group
        if not AuthorizationHelpers.can_manage_group(group):
            if len(user_changes["members_to_add"]) > 0 or len(user_changes["owners_to_add"]) > 0:
                abort(
                    403,
                    "Current user is not allowed to add members to this group",
                )
            if len(user_changes["members_to_remove"]) > 0 or len(user_changes["owners_to_remove"]) > 0:
                for user_id in user_changes["members_to_remove"] + user_changes["owners_to_remove"]:
                    # Allow current user to remove themselves from the group
                    if user_id != g.current_user_id:
                        abort(403, "Current user is not allowed to perform this action")

        # Check groups tags to see if self-add is allowed
        valid, err_message = CheckForSelfAdd(
            group=group,
            current_user=g.current_user_id,
            members_to_add=user_changes["members_to_add"],
            owners_to_add=user_changes["owners_to_add"],
        ).execute_for_group()
        if not valid:
            abort(400, err_message)

        # Check group tags to see if a reason is required for adding members or owners
        valid, err_message = CheckForReason(
            group=group,
            reason=user_changes.get("created_reason"),
            members_to_add=user_changes["members_to_add"],
            owners_to_add=user_changes["owners_to_add"],
        ).execute_for_group()
        if not valid:
            abort(400, err_message)

        # Modify group users
        ModifyGroupUsers(
            group=group,
            current_user_id=g.current_user_id,
            users_added_ended_at=user_changes.get("users_added_ending_at"),
            members_to_add=user_changes["members_to_add"],
            owners_to_add=user_changes["owners_to_add"],
            members_should_expire=user_changes.get("members_should_expire", []),
            owners_should_expire=user_changes.get("owners_should_expire", []),
            members_to_remove=user_changes["members_to_remove"],
            owners_to_remove=user_changes["owners_to_remove"],
            created_reason=user_changes.get("created_reason", ""),
        ).execute()

        members = (
            OktaUserGroupMember.query.join(OktaUserGroupMember.active_group)
            .options(joinedload(OktaUserGroupMember.active_group))
            .with_entities(OktaUserGroupMember.user_id)
            .filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaUserGroupMember.group_id == group.id)
            .group_by(OktaUserGroupMember.user_id)
        )
        return schema.dump(
            {
                "members": [m.user_id for m in members.filter(OktaUserGroupMember.is_owner.is_(False)).all()],
                "owners": [m.user_id for m in members.filter(OktaUserGroupMember.is_owner.is_(True)).all()],
            }
        )


class GroupList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchGroupPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(GroupPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchGroupPaginationRequestSchema().load(request.args)

        query = (
            db.session.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                selectinload(OktaGroup.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_app_tag_mapping),
                    joinedload(OktaGroupTagMap.active_tag),
                ),
                joinedload(AppGroup.app),
                selectinload(RoleGroup.active_role_associated_group_member_mappings).options(
                    joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                        selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                            joinedload(OktaGroupTagMap.active_tag), joinedload(OktaGroupTagMap.active_app_tag_mapping)
                        ),
                        joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
                    ),
                ),
                selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(
                    joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                        selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                            joinedload(OktaGroupTagMap.active_tag), joinedload(OktaGroupTagMap.active_app_tag_mapping)
                        ),
                        joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
                    ),
                ),
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .order_by(func.lower(OktaGroup.name))
        )

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            query = query.filter(
                db.or_(
                    OktaGroup.name.ilike(like_search),
                    OktaGroup.description.ilike(like_search),
                )
            )

        if "managed" in search_args:
            query = query.filter(OktaGroup.is_managed == search_args["managed"])

        return paginate(
            query,
            PolymorphicGroupSchema(
                many=True,
                only=(
                    "id",
                    "type",
                    "name",
                    "description",
                    "created_at",
                    "updated_at",
                    "is_managed",
                    "active_group_tags",
                    "app.id",
                    "active_role_associated_group_member_mappings",
                    "active_role_associated_group_owner_mappings",
                ),
            ),
        )

    @FlaskApiSpecDecorators.request_schema(PolymorphicGroupSchema)
    @FlaskApiSpecDecorators.response_schema(PolymorphicGroupSchema)
    def post(self) -> ResponseReturnValue:
        schema = PolymorphicGroupSchema(exclude=DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS)
        group = schema.load(request.json)

        # Only allow if the current user is an Access admin or app owner (if it's an app group)
        if not (AuthorizationHelpers.is_access_admin() or AuthorizationHelpers.is_app_owner_group_owner(group)):
            abort(403, "Current user is not allowed to perform this action")

        # Do not allow non-deleted groups with the same name (case-insensitive)
        existing_group = (
            db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(func.lower(OktaGroup.name) == func.lower(group.name))
            .filter(OktaGroup.deleted_at.is_(None))
            .first()
        )
        if existing_group is not None:
            abort(400, "Group already exists with the same name")

        group = CreateGroup(
            group=group, tags=request.get_json().get("tags_to_add", []), current_user_id=g.current_user_id
        ).execute()

        group = (
            db.session.query(OktaGroup)
            .options(DEFAULT_LOAD_OPTIONS)
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == group.id)
            .first()
        )
        return schema.dump(group), 201
