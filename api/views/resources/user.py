import re

from flask import current_app, g, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import func, nullsfirst
from sqlalchemy.sql import sqltypes
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.apispec import FlaskApiSpecDecorators
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
    OktaUserSchema,
    SearchPaginationRequestSchema,
    UserPaginationSchema,
)

ALL_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup, RoleGroup])


class UserResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(OktaUserSchema)
    def get(self, user_id: str) -> ResponseReturnValue:
        # A reserved "@me" user_id API lookup returns data for the current user
        if user_id == "@me":
            user_id = g.current_user_id

        schema = OktaUserSchema(
            exclude=(
                "all_group_memberships_and_ownerships",
                "active_group_memberships_and_ownerships",
            )
        )

        user = (
            OktaUser.query
            # Use selectinload for one-to-many eager loading and used joinedload for one-to-one eager loading
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                selectinload(OktaUser.active_group_memberships).options(
                    joinedload(OktaUserGroupMember.active_group.of_type(ALL_GROUP_TYPES)).joinedload(
                        ALL_GROUP_TYPES.AppGroup.app
                    ),
                    joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
                        RoleGroupMap.active_role_group
                    ),
                ),
                selectinload(OktaUser.active_group_ownerships).options(
                    joinedload(OktaUserGroupMember.active_group.of_type(ALL_GROUP_TYPES)).options(
                        joinedload(ALL_GROUP_TYPES.AppGroup.app),
                        selectinload(ALL_GROUP_TYPES.active_group_tags).options(
                            joinedload(OktaGroupTagMap.active_tag), joinedload(OktaGroupTagMap.active_app_tag_mapping)
                        ),
                    ),
                    joinedload(OktaUserGroupMember.active_role_group_mapping).joinedload(
                        RoleGroupMap.active_role_group
                    ),
                ),
                joinedload(OktaUser.manager),
            )
            .filter(db.or_(OktaUser.id == user_id, OktaUser.email.ilike(user_id)))
            .order_by(nullsfirst(OktaUser.deleted_at.desc()))
            .first_or_404()
        )
        return schema.dump(user)


class UserAuditResource(MethodResource):
    def get(self, user_id: str) -> ResponseReturnValue:
        return redirect(
            url_for(
                "api-audit.users_and_groups",
                _anchor=None,
                _method=None,
                _scheme=None,
                _external=None,  # To pass type checking
                user_id=user_id,
                **request.args,
            )
        )


class UserList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(SearchPaginationRequestSchema, location="query")
    @FlaskApiSpecDecorators.response_schema(UserPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchPaginationRequestSchema().load(request.args)

        query = OktaUser.query.filter(OktaUser.deleted_at.is_(None)).order_by(func.lower(OktaUser.email))

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            if db.engine.name == "postgresql":
                # Search across the USER_SEARCH_CUSTOM_ATTRIBUTE values (not keys) in the JSON profile fields
                # for PostgreSQL
                #
                # Helpful links:
                # https://stackoverflow.com/a/73346053
                # https://stackoverflow.com/a/63742174
                # https://www.postgresql.org/docs/current/functions-json.html#FUNCTIONS-SQLJSON-PATH

                # Escape the search query regex params and double escape the resultant backslashes
                query_regex_escaped = f'.*{re.escape(search_args["q"])}.*'.replace("\\", "\\\\")
                # Escape any quotes in the search query regex to not break the jsonpath query
                query_regex_quote_escaped = query_regex_escaped.replace('"', '\\"')
                # Build the jsonpath query to search the custom attributes
                search_attributes = current_app.config["USER_SEARCH_CUSTOM_ATTRIBUTES"].split(",")
                attr_query = [f'@."{attr}" like_regex "%s" flag "i"' for attr in search_attributes]
                # Combine the jsonpath queries with an OR operator
                search_jsonpath = f'strict $ ? ({" || ".join(attr_query)})'
                format_params = [query_regex_quote_escaped] * len(search_attributes)
                query = query.filter(
                    db.or_(
                        OktaUser.email.ilike(like_search),
                        OktaUser.first_name.ilike(like_search),
                        OktaUser.last_name.ilike(like_search),
                        OktaUser.display_name.ilike(like_search),
                        (OktaUser.first_name + " " + OktaUser.last_name).ilike(like_search),
                        # An injection is potentially possible here however it is limited to inside the
                        # format of the jsonpath query which cannot update the profile field and we escape
                        # the search query to prevent any other injection
                        #
                        # Helpful links:
                        # https://stackoverflow.com/a/76671897
                        # https://justatheory.com/2023/10/sql-jsonpath-operators/
                        # https://www.sqliz.com/postgresql-ref/jsonb_path_exists/
                        #
                        # Normally we'd want to use variable substitution as a third parameter for jsonb_path_exists
                        # function, however like_regex only accepts jsonpath string literals and not variables
                        # so we have to use FORMAT() instead
                        #
                        # Helpful links:
                        # https://stackoverflow.com/a/77318568
                        # https://dba.stackexchange.com/a/332283
                        # https://www.postgresql.org/docs/current/functions-json.html#JSONPATH-REGULAR-EXPRESSIONS
                        func.jsonb_path_exists(
                            OktaUser.profile,
                            db.cast(func.format(search_jsonpath, *format_params), sqltypes.JSON.JSONPathType),
                            func.jsonb_build_object(),
                            True,  # Silence any errors similar to using @? operator
                        ),
                    )
                )
            else:
                # Naive search of JSON field (searches both keys and values) in the JSON profile fields for SQLite
                query = query.filter(
                    db.or_(
                        OktaUser.email.ilike(like_search),
                        OktaUser.first_name.ilike(like_search),
                        OktaUser.last_name.ilike(like_search),
                        OktaUser.display_name.ilike(like_search),
                        (OktaUser.first_name + " " + OktaUser.last_name).ilike(like_search),
                        OktaUser.profile.ilike(like_search),
                    )
                )

        return paginate(
            query,
            OktaUserSchema(
                many=True,
                only=(
                    "id",
                    "created_at",
                    "updated_at",
                    "email",
                    "first_name",
                    "last_name",
                    "display_name",
                ),
            ),
        )
