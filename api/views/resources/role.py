from flask import abort, g, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationHelpers
from api.extensions import db
from api.models import AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap
from api.operations import ModifyRoleGroups
from api.operations.constraints import CheckForReason, CheckForSelfAdd
from api.pagination import paginate
from api.views.schemas import (
    RoleGroupSchema,
    RoleMemberSchema,
    RolePaginationSchema,
    SearchRolePaginationRequestSchema,
)

ALL_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup, RoleGroup], flat=True)
ROLE_ASSOCIATED_GROUP_TYPES = with_polymorphic(
    OktaGroup,
    [
        AppGroup,
    ],
)


class RoleResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(RoleGroupSchema)
    def get(self, role_id: str) -> ResponseReturnValue:
        schema = RoleGroupSchema(
            exclude=(
                "all_user_memberships_and_ownerships",
                "active_user_memberships_and_ownerships",
                "all_role_associated_group_mappings",
                "active_role_associated_group_mappings",
                "all_group_tags",
            )
        )
        # Use selectinload for one-to-many eager loading and used joinedload for one-to-one eager loading
        role = (
            RoleGroup.query.options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
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
                selectinload(RoleGroup.active_user_memberships).joinedload(OktaUserGroupMember.active_user),
                selectinload(RoleGroup.active_user_ownerships).joinedload(OktaUserGroupMember.active_user),
                selectinload(RoleGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
            )
            .filter(db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
            .order_by(nullsfirst(RoleGroup.deleted_at.desc()))
            .first_or_404()
        )
        return schema.dump(role)


class RoleAuditResource(MethodResource):
    def get(self, role_id: str) -> ResponseReturnValue:
        return redirect(
            url_for(
                "api-audit.groups_and_roles",
                _anchor=None,
                _method=None,
                _scheme=None,
                _external=None,  # To pass type checking
                role_id=role_id,
                **request.args,
            )
        )


class RoleMemberResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(RoleMemberSchema)
    def get(self, role_id: str) -> ResponseReturnValue:
        # Check to make sure this Role exists
        role = (
            RoleGroup.query.filter(RoleGroup.deleted_at.is_(None))
            .filter(db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
            .first_or_404()
        )

        schema = RoleMemberSchema()
        members = (
            RoleGroupMap.query.join(RoleGroupMap.active_role_group)
            .options(joinedload(RoleGroupMap.active_role_group))
            .with_entities(RoleGroupMap.group_id)
            .filter(db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > db.func.now()))
            .filter(RoleGroup.deleted_at.is_(None))
            .filter(RoleGroupMap.role_group_id == role.id)
            .group_by(RoleGroupMap.group_id)
        )
        return schema.dump(
            {
                "groups_in_role": [m.group_id for m in members.filter(RoleGroupMap.is_owner.is_(False)).all()],
                "groups_owned_by_role": [m.group_id for m in members.filter(RoleGroupMap.is_owner.is_(True)).all()],
            }
        )

    @FlaskApiSpecDecorators.request_schema(RoleMemberSchema)
    @FlaskApiSpecDecorators.response_schema(RoleMemberSchema)
    def put(self, role_id: str) -> ResponseReturnValue:
        role = (
            RoleGroup.query.filter(RoleGroup.deleted_at.is_(None))
            .filter(db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
            .first_or_404()
        )

        schema = RoleMemberSchema()
        group_changes = schema.load(request.get_json())

        # If they're an Access admin they can modify any role
        if not AuthorizationHelpers.is_access_admin():
            groups = (
                db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(OktaGroup.deleted_at.is_(None))
                .filter(OktaGroup.id.in_(group_changes["groups_to_add"] + group_changes["owner_groups_to_add"]))
            )
            for group in groups:
                # Check each group being added to make sure the current user is an owner of the group
                # or the owner of the associated app
                if not AuthorizationHelpers.is_group_owner(group) and not AuthorizationHelpers.is_app_owner_group_owner(
                    app_group=group
                ):
                    abort(403, "Current user is not allowed to perform this action")

            # If they're the role owner than they can remove any group from the role
            if not AuthorizationHelpers.is_group_owner(role):
                groups = (
                    db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                    .filter(OktaGroup.deleted_at.is_(None))
                    .filter(
                        OktaGroup.id.in_(group_changes["groups_to_remove"] + group_changes["owner_groups_to_remove"])
                    )
                )
                for group in groups:
                    # Check each group being removed to make sure the current user is an owner of the group
                    # or the owner of the associated app
                    if not AuthorizationHelpers.is_group_owner(
                        group
                    ) and not AuthorizationHelpers.is_app_owner_group_owner(app_group=group):
                        abort(403, "Current user is not allowed to perform this action")

        if (
            db.session.query(db.func.count(OktaGroup.id))
            .filter(
                OktaGroup.id.in_(
                    group_changes["groups_to_add"]
                    + group_changes["owner_groups_to_add"]
                    + group_changes["groups_to_remove"]
                    + group_changes["owner_groups_to_remove"]
                )
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.is_managed.is_(False))
            .scalar()
            > 0
        ):
            abort(
                400,
                "Groups not managed by Access cannot be modified",
            )

        if (
            db.session.query(db.func.count(OktaGroup.id))
            .filter(OktaGroup.id.in_(group_changes["groups_to_add"] + group_changes["owner_groups_to_add"]))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.type == RoleGroup.__mapper_args__["polymorphic_identity"])
            .scalar()
            > 0
        ):
            abort(
                400,
                "Roles cannot be added to other Roles",
            )

        # Check group tags on groups being added to see if current user isn't adding themselves as member or owner
        valid, err_message = CheckForSelfAdd(
            group=role,
            current_user=g.current_user_id,
            members_to_add=group_changes["groups_to_add"],
            owners_to_add=group_changes["owner_groups_to_add"],
        ).execute_for_role()
        if not valid:
            abort(400, err_message)

        # Check group tags on groups being added to see if a reason is required
        valid, err_message = CheckForReason(
            group=role,
            reason=group_changes.get("created_reason"),
            members_to_add=group_changes["groups_to_add"],
            owners_to_add=group_changes["owner_groups_to_add"],
        ).execute_for_role()
        if not valid:
            abort(400, err_message)

        ModifyRoleGroups(
            role_group=role,
            current_user_id=g.current_user_id,
            groups_added_ended_at=group_changes.get("groups_added_ending_at"),
            groups_to_add=group_changes["groups_to_add"],
            owner_groups_to_add=group_changes["owner_groups_to_add"],
            groups_should_expire=group_changes.get("groups_should_expire", []),
            owner_groups_should_expire=group_changes.get("owner_groups_should_expire", []),
            groups_to_remove=group_changes["groups_to_remove"],
            owner_groups_to_remove=group_changes["owner_groups_to_remove"],
            created_reason=group_changes.get("created_reason", ""),
        ).execute()

        members = (
            RoleGroupMap.query.join(RoleGroupMap.active_role_group)
            .options(joinedload(RoleGroupMap.active_role_group))
            .with_entities(RoleGroupMap.group_id)
            .filter(db.or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > db.func.now()))
            .filter(RoleGroup.deleted_at.is_(None))
            .filter(RoleGroupMap.role_group_id == role.id)
            .group_by(RoleGroupMap.group_id)
        )
        return schema.dump(
            {
                "groups_in_role": [m.group_id for m in members.filter(RoleGroupMap.is_owner.is_(False)).all()],
                "groups_owned_by_role": [m.group_id for m in members.filter(RoleGroupMap.is_owner.is_(True)).all()],
            }
        )


class RoleList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchRolePaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(RolePaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchRolePaginationRequestSchema().load(request.args)

        query = RoleGroup.query.filter(RoleGroup.deleted_at.is_(None)).order_by(func.lower(RoleGroup.name))

        if "owner_id" in search_args:
            owner_id = search_args["owner_id"]
            if owner_id == "@me":
                owner_id = g.current_user_id
            owner = (
                OktaUser.query.filter(db.or_(OktaUser.id == owner_id, OktaUser.email.ilike(owner_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first_or_404()
            )
            owner_group_ownerships = (
                OktaUserGroupMember.query.filter(OktaUserGroupMember.user_id == owner.id)
                .filter(OktaUserGroupMember.is_owner.is_(True))
                .filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
            )
            query = query.filter(
                RoleGroup.id.in_(
                    [o.group_id for o in owner_group_ownerships.with_entities(OktaUserGroupMember.group_id).all()]
                ),
            )

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            query = query.filter(
                db.or_(
                    RoleGroup.name.ilike(like_search),
                    RoleGroup.description.ilike(like_search),
                )
            )

        return paginate(
            query,
            RoleGroupSchema(
                many=True,
                only=(
                    "id",
                    "type",
                    "name",
                    "description",
                    "created_at",
                    "updated_at",
                ),
            ),
        )
