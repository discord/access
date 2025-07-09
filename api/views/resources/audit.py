from datetime import datetime

from flask import g, request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import func, nullsfirst, nullslast
from sqlalchemy.orm import aliased, joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationHelpers
from api.extensions import db
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
from api.views.schemas import (
    GroupRoleAuditPaginationSchema,
    OktaUserGroupMemberSchema,
    RoleGroupMapSchema,
    SearchGroupRoleAuditPaginationRequestSchema,
    SearchUserGroupAuditPaginationRequestSchema,
    UserGroupAuditPaginationSchema,
)

ROLE_ASSOCIATED_GROUP_TYPES = with_polymorphic(
    OktaGroup,
    [
        AppGroup,
    ],
)


class UserGroupAuditResource(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchUserGroupAuditPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(UserGroupAuditPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchUserGroupAuditPaginationRequestSchema().load(request.args)
        additional_schema_fields = []

        group_alias = aliased(OktaGroup)

        order_by = search_args["order_by"].name
        order_direction = search_args["order_desc"]
        nulls_order = nullsfirst if order_direction else nullslast

        query = (
            OktaUserGroupMember.query.options(
                joinedload(OktaUserGroupMember.access_request),
                joinedload(OktaUserGroupMember.user),
                joinedload(OktaUserGroupMember.created_actor),
                joinedload(OktaUserGroupMember.ended_actor),
                selectinload(OktaUserGroupMember.group).options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    joinedload(AppGroup.app),
                    selectinload(OktaGroup.active_group_tags).options(
                        joinedload(OktaGroupTagMap.active_tag),
                        joinedload(OktaGroupTagMap.active_app_tag_mapping),
                    ),
                    joinedload(RoleGroup.active_role_associated_group_member_mappings).options(
                        joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                            selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                                joinedload(OktaGroupTagMap.active_tag),
                                joinedload(OktaGroupTagMap.active_app_tag_mapping),
                            ),
                            joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
                        )
                    ),
                    joinedload(RoleGroup.active_role_associated_group_owner_mappings).options(
                        joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                            selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                                joinedload(OktaGroupTagMap.active_tag),
                                joinedload(OktaGroupTagMap.active_app_tag_mapping),
                            ),
                            joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
                        )
                    ),
                ),
                selectinload(OktaUserGroupMember.role_group_mapping).joinedload(RoleGroupMap.role_group),
            )
            .join(OktaUserGroupMember.user)
            .join(OktaUserGroupMember.group.of_type(group_alias))
        )

        if "user_id" in search_args:
            user_id = search_args["user_id"]
            # A reserved "@me" user_id API lookup returns data for the current user
            if user_id == "@me":
                user_id = g.current_user_id
            user = (
                OktaUser.query.filter(db.or_(OktaUser.id == user_id, OktaUser.email.ilike(user_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first_or_404()
            )
            query = (
                query.filter(OktaUserGroupMember.user_id == user.id)
                # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
                .order_by(
                    nulls_order(
                        getattr(
                            group_alias.name if order_by == "moniker" else getattr(OktaUserGroupMember, order_by),
                            "desc" if order_direction else "asc",
                        )()
                    ),
                    group_alias.name.asc()
                    if order_by != "moniker"
                    else nullslast(OktaUserGroupMember.created_at.asc()),
                )
            )

        if "group_id" in search_args:
            group_id = search_args["group_id"]
            group = (
                db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
                .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
                .first_or_404()
            )
            query = (
                query.filter(OktaUserGroupMember.group_id == group.id)
                # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
                .order_by(
                    nulls_order(
                        getattr(
                            func.lower(OktaUser.email)
                            if order_by == "moniker"
                            else getattr(OktaUserGroupMember, order_by),
                            "desc" if order_direction else "asc",
                        )()
                    ),
                    func.lower(OktaUser.email).asc()
                    if order_by != "moniker"
                    else nullslast(OktaUserGroupMember.created_at.asc()),
                )
            )

        if "group_id" not in search_args and "user_id" not in search_args:
            # SQLAlchemy doesn't seem to support loading
            # group.active_role_associated_group_[member|owner]_mappings.active_group when a group_id is specified
            # so we have do not include it then. If we're only searching by group_id, we can lookup the group tags
            # and tags associated with associated groups if it's a role by calling GET /api/groups/<group_id> instead.
            additional_schema_fields.extend(
                [
                    "group.active_role_associated_group_member_mappings",
                    "group.active_role_associated_group_owner_mappings",
                ]
            )

        if "owner_id" in search_args:
            owner_id = search_args["owner_id"]
            if owner_id == "@me":
                owner_id = g.current_user_id
            owner = (
                OktaUser.query.filter(db.or_(OktaUser.id == owner_id, OktaUser.email.ilike(owner_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first_or_404()
            )
            app_group_alias = aliased(AppGroup)
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
            app_owner_group_ownerships = (
                owner_group_ownerships.options(joinedload(OktaUserGroupMember.group.of_type(AppGroup)))
                .join(OktaUserGroupMember.group.of_type(app_group_alias))
                .filter(app_group_alias.is_owner.is_(True))
            )
            app_groups_owned = AppGroup.query.filter(
                AppGroup.app_id.in_([o.group.app_id for o in app_owner_group_ownerships.all()])
            )

            app_groups_owned_ids = set(g.id for g in app_groups_owned.with_entities(AppGroup.id).all())

            # Do not display App groups that have at least one direct Owner
            app_groups_directly_owned_by_others = (
                OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id.in_(app_groups_owned_ids))
                .filter(OktaUserGroupMember.is_owner.is_(True))
                .filter(OktaUserGroupMember.user_id != owner.id)
                .filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
            )
            app_groups_directly_owned_by_others_ids = set(
                g.group_id
                for g in app_groups_directly_owned_by_others.with_entities(OktaUserGroupMember.group_id).all()
            )

            query = (
                query.filter(
                    db.or_(
                        OktaUserGroupMember.group_id.in_(
                            [
                                o.group_id
                                for o in owner_group_ownerships.with_entities(OktaUserGroupMember.group_id).all()
                            ]
                        ),
                        OktaUserGroupMember.group_id.in_(
                            app_groups_owned_ids - app_groups_directly_owned_by_others_ids
                        ),
                    )
                )
                .filter(OktaUserGroupMember.user_id != owner_id)
                # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
                .order_by(
                    nulls_order(
                        getattr(
                            func.lower(OktaUser.email)
                            if order_by == "moniker"
                            else getattr(OktaUserGroupMember, order_by),
                            "desc" if order_direction else "asc",
                        )()
                    ),
                    func.lower(OktaUser.email).asc()
                    if order_by != "moniker"
                    else nullslast(OktaUserGroupMember.created_at.asc()),
                )
            )

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            if "user_id" in search_args:
                query = query.filter(
                    db.or_(
                        group_alias.id.ilike(like_search),
                        group_alias.name.ilike(like_search),
                        group_alias.description.ilike(like_search),
                    )
                )

            if "group_id" in search_args:
                query = query.filter(
                    db.or_(
                        OktaUser.id.ilike(like_search),
                        OktaUser.email.ilike(like_search),
                        OktaUser.first_name.ilike(like_search),
                        OktaUser.last_name.ilike(like_search),
                        OktaUser.display_name.ilike(like_search),
                        (OktaUser.first_name + " " + OktaUser.last_name).ilike(like_search),
                    )
                )

            if "owner_id" in search_args or ("user_id" not in search_args and "group_id" not in search_args):
                query = query.filter(
                    db.or_(
                        group_alias.id.ilike(like_search),
                        group_alias.name.ilike(like_search),
                        group_alias.description.ilike(like_search),
                        OktaUser.id.ilike(like_search),
                        OktaUser.email.ilike(like_search),
                        OktaUser.first_name.ilike(like_search),
                        OktaUser.last_name.ilike(like_search),
                        OktaUser.display_name.ilike(like_search),
                        (OktaUser.first_name + " " + OktaUser.last_name).ilike(like_search),
                    )
                )

        if "owner" in search_args:
            query = query.filter(OktaUserGroupMember.is_owner == search_args["owner"])

        if "active" in search_args:
            if search_args["active"]:
                query = query.filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
            else:
                query = query.filter(
                    db.and_(
                        OktaUserGroupMember.ended_at.is_not(None),
                        OktaUserGroupMember.ended_at < db.func.now(),
                    )
                )
        if "needs_review" in search_args:
            if search_args["needs_review"]:
                query = query.filter(
                    OktaUserGroupMember.should_expire.is_(False),
                )

        if "direct" in search_args:
            if search_args["direct"]:
                query = query.filter(db.not_(OktaUserGroupMember.active_role_group_mapping.has()))

            if "user_id" not in search_args and "owner_id" not in search_args:
                query = query.order_by(
                    nulls_order(
                        getattr(
                            func.lower(OktaUser.email)
                            if order_by == "moniker"
                            else getattr(OktaUserGroupMember, order_by),
                            "desc" if order_direction else "asc",
                        )()
                    ),
                    func.lower(OktaUser.email).asc()
                    if order_by != "moniker"
                    else nullslast(OktaUserGroupMember.created_at.asc()),
                )

        if "deleted" in search_args:
            if not search_args["deleted"]:
                query = query.filter(OktaUser.deleted_at.is_(None))

        if "start_date" in search_args and "end_date" in search_args:
            query = query.filter(
                db.and_(
                    OktaUserGroupMember.ended_at.is_not(None),
                    OktaUserGroupMember.ended_at > datetime.fromtimestamp(search_args["start_date"]),
                    OktaUserGroupMember.ended_at < datetime.fromtimestamp(search_args["end_date"]),
                )
            )

        if "managed" in search_args:
            query = query.filter(group_alias.is_managed == search_args["managed"])

        return paginate(
            query,
            OktaUserGroupMemberSchema(
                many=True,
                only=[
                    "id",
                    "created_at",
                    "ended_at",
                    "created_reason",
                    "is_owner",
                    "should_expire",
                    "access_request.id",
                    "user.id",
                    "user.created_at",
                    "user.deleted_at",
                    "user.email",
                    "user.first_name",
                    "user.last_name",
                    "user.display_name",
                    "group.deleted_at",
                    "group.id",
                    "group.type",
                    "group.name",
                    "group.is_owner",
                    "group.is_managed",
                    "group.active_group_tags",
                    "group.app.id",
                    "group.app.name",
                    "group.app.deleted_at",
                    "role_group_mapping.created_at",
                    "role_group_mapping.ended_at",
                    "role_group_mapping.role_group.deleted_at",
                    "role_group_mapping.role_group.id",
                    "role_group_mapping.role_group.type",
                    "role_group_mapping.role_group.name",
                    "created_actor.id",
                    "created_actor.email",
                    "created_actor.deleted_at",
                    "created_actor.first_name",
                    "created_actor.last_name",
                    "ended_actor.id",
                    "ended_actor.email",
                    "ended_actor.deleted_at",
                    "ended_actor.first_name",
                    "ended_actor.last_name",
                ]
                + additional_schema_fields,
            ),
        )


class GroupRoleAuditResource(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchGroupRoleAuditPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(GroupRoleAuditPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchGroupRoleAuditPaginationRequestSchema().load(request.args)

        group_alias = aliased(OktaGroup)
        order_by = search_args["order_by"].name
        order_direction = search_args["order_desc"]
        nulls_order = nullsfirst if order_direction else nullslast
        query = (
            RoleGroupMap.query.options(
                joinedload(RoleGroupMap.group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                    joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app),
                    joinedload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                        joinedload(OktaGroupTagMap.active_tag),
                        joinedload(OktaGroupTagMap.active_app_tag_mapping),
                    ),
                ),
                joinedload(RoleGroupMap.role_group),
                joinedload(RoleGroupMap.created_actor),
                joinedload(RoleGroupMap.ended_actor),
            )
            .join(RoleGroupMap.role_group)
            .join(RoleGroupMap.group.of_type(group_alias))
        )

        if "group_id" in search_args:
            group_id = search_args["group_id"]
            group = (
                db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
                .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
                .first_or_404()
            )
            query = (
                query.filter(RoleGroupMap.group_id == group.id)
                # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
                .order_by(
                    nulls_order(
                        getattr(
                            RoleGroup.name if order_by == "moniker" else getattr(RoleGroupMap, order_by),
                            "desc" if order_direction else "asc",
                        )()
                    ),
                    RoleGroup.name.asc() if order_by != "moniker" else nullslast(RoleGroupMap.created_at.asc()),
                )
            )

        if "role_id" in search_args:
            role_id = search_args["role_id"]
            role = (
                RoleGroup.query.filter(db.or_(RoleGroup.id == role_id, RoleGroup.name == role_id))
                .order_by(nullsfirst(RoleGroup.deleted_at.desc()))
                .first_or_404()
            )
            query = (
                query.filter(RoleGroupMap.role_group_id == role.id)
                # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
                .order_by(
                    nulls_order(
                        getattr(
                            group_alias.name if order_by == "moniker" else getattr(RoleGroupMap, order_by),
                            "desc" if order_direction else "asc",
                        )()
                    ),
                    group_alias.name.asc() if order_by != "moniker" else nullslast(RoleGroupMap.created_at.asc()),
                )
            )

        if "owner_id" in search_args:
            owner_id = search_args["owner_id"]
            if owner_id == "@me":
                owner_id = g.current_user_id
            owner = (
                OktaUser.query.filter(db.or_(OktaUser.id == owner_id, OktaUser.email.ilike(owner_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first_or_404()
            )
            app_group_alias = aliased(AppGroup)
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
            app_owner_group_ownerships = (
                owner_group_ownerships.options(joinedload(OktaUserGroupMember.group.of_type(AppGroup)))
                .join(OktaUserGroupMember.group.of_type(app_group_alias))
                .filter(app_group_alias.is_owner.is_(True))
            )
            app_groups_owned = AppGroup.query.filter(
                AppGroup.app_id.in_([o.group.app_id for o in app_owner_group_ownerships.all()])
            )
            app_groups_owned_ids = set(g.id for g in app_groups_owned.with_entities(AppGroup.id).all())

            # Do not display App groups that have at least one direct Owner
            if "app_owner" not in search_args or search_args["app_owner"] is False:
                app_groups_directly_owned_by_others = (
                    OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id.in_(app_groups_owned_ids))
                    .filter(OktaUserGroupMember.user_id != owner.id)
                    .filter(OktaUserGroupMember.is_owner.is_(True))
                    .filter(
                        db.or_(
                            OktaUserGroupMember.ended_at.is_(None),
                            OktaUserGroupMember.ended_at > db.func.now(),
                        )
                    )
                )
                app_groups_directly_owned_by_others_ids = set(
                    g.group_id
                    for g in app_groups_directly_owned_by_others.with_entities(OktaUserGroupMember.group_id).all()
                )

                query = query.filter(
                    db.or_(
                        RoleGroupMap.group_id.in_(
                            [
                                o.group_id
                                for o in owner_group_ownerships.with_entities(OktaUserGroupMember.group_id).all()
                            ]
                        ),
                        RoleGroupMap.group_id.in_(app_groups_owned_ids - app_groups_directly_owned_by_others_ids),
                    )
                )

            else:
                query = query.filter(
                    db.or_(
                        RoleGroupMap.group_id.in_(
                            [
                                o.group_id
                                for o in owner_group_ownerships.with_entities(OktaUserGroupMember.group_id).all()
                            ]
                        ),
                        RoleGroupMap.group_id.in_(app_groups_owned_ids),
                    )
                )

            # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
            query = query.order_by(
                nulls_order(
                    getattr(
                        group_alias.name if order_by == "moniker" else getattr(RoleGroupMap, order_by),
                        "desc" if order_direction else "asc",
                    )()
                ),
                group_alias.name.asc() if order_by != "moniker" else nullslast(RoleGroupMap.created_at.asc()),
            )

        if "role_owner_id" in search_args:
            role_owner_id = search_args["role_owner_id"]
            if role_owner_id == "@me":
                role_owner_id = g.current_user_id
            role_owner = (
                OktaUser.query.filter(db.or_(OktaUser.id == role_owner_id, OktaUser.email.ilike(role_owner_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first_or_404()
            )
            role_group_alias = aliased(RoleGroup)

            # get current role ownerships
            owner_role_ownerships = (
                OktaUserGroupMember.query.options(joinedload(OktaUserGroupMember.group.of_type(RoleGroup)))
                .filter(OktaUserGroupMember.user_id == role_owner.id)
                .filter(OktaUserGroupMember.is_owner.is_(True))
                .join(OktaUserGroupMember.group.of_type(role_group_alias))
                .filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
            )

            # if user is an admin, include unowned roles with expiring access
            unowned_roles_admin = []
            if AuthorizationHelpers.is_access_admin(role_owner_id):
                owners_subquery = (
                    db.session.query(OktaUserGroupMember.group_id)
                    .filter(
                        db.and_(
                            OktaUserGroupMember.is_owner.is_(True),
                            db.or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()),
                        )
                    )
                    .subquery()
                )

                unowned_roles_admin = RoleGroup.query.filter(
                    db.and_(RoleGroup.deleted_at.is_(None), ~RoleGroup.id.in_(owners_subquery))
                ).all()

            # https://stackoverflow.com/questions/4186062/sqlalchemy-order-by-descending#comment52902932_9964966
            query = query.filter(
                db.or_(
                    RoleGroupMap.role_group_id.in_(
                        [o.group_id for o in owner_role_ownerships.with_entities(OktaUserGroupMember.group_id).all()]
                    ),
                    RoleGroupMap.role_group_id.in_([rgm.id for rgm in unowned_roles_admin]),
                )
            ).order_by(
                nulls_order(
                    getattr(
                        group_alias.name if order_by == "moniker" else getattr(RoleGroupMap, order_by),
                        "desc" if order_direction else "asc",
                    )()
                ),
                group_alias.name.asc() if order_by != "moniker" else nullslast(RoleGroupMap.created_at.asc()),
            )

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            if "group_id" in search_args:
                query = query.filter(
                    db.or_(
                        RoleGroup.id.ilike(like_search),
                        RoleGroup.name.ilike(like_search),
                        RoleGroup.description.ilike(like_search),
                    )
                )

            if "role_id" in search_args:
                query = query.filter(
                    db.or_(
                        group_alias.id.ilike(like_search),
                        group_alias.name.ilike(like_search),
                        group_alias.description.ilike(like_search),
                    )
                )

            if "owner_id" in search_args or ("group_id" not in search_args and "role_id" not in search_args):
                query = query.filter(
                    db.or_(
                        RoleGroup.id.ilike(like_search),
                        RoleGroup.name.ilike(like_search),
                        RoleGroup.description.ilike(like_search),
                        group_alias.id.ilike(like_search),
                        group_alias.name.ilike(like_search),
                        group_alias.description.ilike(like_search),
                    )
                )

        if "group_id" not in search_args and "role_id" not in search_args and "owner_id" not in search_args:
            query = query.order_by(
                nulls_order(
                    getattr(
                        group_alias.name if order_by == "moniker" else getattr(RoleGroupMap, order_by),
                        "desc" if order_direction else "asc",
                    )()
                ),
                group_alias.name.asc() if order_by != "moniker" else nullslast(RoleGroupMap.created_at.asc()),
            )

        if "start_date" in search_args and "end_date" in search_args:
            query = query.filter(
                db.and_(
                    RoleGroupMap.ended_at.is_not(None),
                    RoleGroupMap.ended_at > datetime.fromtimestamp(search_args["start_date"]),
                    RoleGroupMap.ended_at < datetime.fromtimestamp(search_args["end_date"]),
                )
            )

        if "managed" in search_args:
            query = query.filter(group_alias.is_managed == search_args["managed"])

        if "owner" in search_args:
            query = query.filter(RoleGroupMap.is_owner == search_args["owner"])

        if "active" in search_args:
            if search_args["active"]:
                query = query.filter(
                    db.or_(
                        RoleGroupMap.ended_at.is_(None),
                        RoleGroupMap.ended_at > db.func.now(),
                    )
                )
            else:
                query = query.filter(
                    db.and_(
                        RoleGroupMap.ended_at.is_not(None),
                        RoleGroupMap.ended_at < db.func.now(),
                    )
                )
        if "needs_review" in search_args:
            if search_args["needs_review"]:
                query = query.filter(
                    RoleGroupMap.should_expire.is_(False),
                )

        return paginate(
            query,
            RoleGroupMapSchema(
                many=True,
                only=(
                    "id",
                    "created_at",
                    "ended_at",
                    "created_reason",
                    "is_owner",
                    "should_expire",
                    "group.deleted_at",
                    "group.id",
                    "group.type",
                    "group.name",
                    "group.is_owner",
                    "group.is_managed",
                    "group.active_group_tags",
                    "group.app.id",
                    "group.app.name",
                    "group.app.deleted_at",
                    "role_group.deleted_at",
                    "role_group.id",
                    "role_group.type",
                    "role_group.name",
                    "role_group.is_managed",
                    "created_actor.id",
                    "created_actor.email",
                    "created_actor.deleted_at",
                    "created_actor.first_name",
                    "created_actor.last_name",
                    "ended_actor.id",
                    "ended_actor.email",
                    "ended_actor.deleted_at",
                    "ended_actor.first_name",
                    "ended_actor.last_name",
                ),
            ),
        )
