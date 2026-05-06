"""Users router. Endpoints:

GET /api/users
GET /api/users/{user_id}              (also accepts "@me")
GET /api/users/{user_id}/audit         redirects to /api/audit/users
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import joinedload, selectinload, with_polymorphic
from sqlalchemy.sql import sqltypes
from starlette.requests import Request

from api.auth.dependencies import CurrentUserId
from api.config import settings
from api.database import DbSession
from api.extensions import db as _db
from api.models import (
    AppGroup,
    OktaGroup,
    OktaUser,
    RoleGroup,
)
from api.pagination import paginate
from api.routers._eager import user_group_member_options
from api.schemas import OktaUserDetail, OktaUserSummary, SearchUserPaginationQuery
from api.schemas._serialize import dump_orm

router = APIRouter(prefix="/api/users", tags=["users"])

ALL_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup, RoleGroup])
_user_adapter = TypeAdapter(OktaUserDetail)
_user_summary_adapter = TypeAdapter(OktaUserSummary)


@router.get("", name="users")
def list_users(
    request: Request,
    db: DbSession,
    current_user_id: CurrentUserId,
    q_args: Annotated[SearchUserPaginationQuery, Query()],
) -> dict[str, Any]:
    query = db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).order_by(func.lower(OktaUser.email))

    if q_args.q:
        like = f"%{q_args.q}%"
        # On Postgres, search the configured custom-attribute keys inside the
        # JSON `profile` column with a JSONPath regex. SQLite (used in tests)
        # falls back to a naive ilike over the whole serialized JSON column.
        if _db.engine.name == "postgresql":
            # Escape regex metacharacters and double-escape backslashes so the
            # injected string is safe inside a `like_regex "..."` literal in
            # the JSONPath. Quotes inside the search term must also be
            # escaped so they don't terminate the literal.
            query_regex_escaped = f".*{re.escape(q_args.q)}.*".replace("\\", "\\\\")
            query_regex_quote_escaped = query_regex_escaped.replace('"', '\\"')
            search_attributes = settings.user_search_attrs
            attr_query = [f'@."{attr}" like_regex "%s" flag "i"' for attr in search_attributes]
            search_jsonpath = f"strict $ ? ({' || '.join(attr_query)})"
            format_params = [query_regex_quote_escaped] * len(search_attributes)
            query = query.filter(
                _db.or_(
                    OktaUser.email.ilike(like),
                    OktaUser.first_name.ilike(like),
                    OktaUser.last_name.ilike(like),
                    OktaUser.display_name.ilike(like),
                    (OktaUser.first_name + " " + OktaUser.last_name).ilike(like),
                    # `like_regex` only accepts string literals — variables
                    # don't work — so the regex is splice-formatted with
                    # FORMAT() at execution time. The regex is escaped above.
                    func.jsonb_path_exists(
                        OktaUser.profile,
                        _db.cast(func.format(search_jsonpath, *format_params), sqltypes.JSON.JSONPathType),
                        func.jsonb_build_object(),
                        True,
                    ),
                )
            )
        else:
            # Naive search of JSON field (matches both keys and values).
            query = query.filter(
                _db.or_(
                    OktaUser.email.ilike(like),
                    OktaUser.first_name.ilike(like),
                    OktaUser.last_name.ilike(like),
                    OktaUser.display_name.ilike(like),
                    (OktaUser.first_name + " " + OktaUser.last_name).ilike(like),
                    OktaUser.profile.cast(sqltypes.String).ilike(like),
                )
            )

    return paginate(request, query, _user_summary_adapter, extract=lambda: (q_args.page, q_args.per_page))


@router.get("/{user_id}", name="user_by_id")
def get_user(user_id: str, db: DbSession, current_user_id: CurrentUserId) -> dict[str, Any]:
    if user_id == "@me":
        user_id = current_user_id

    user = (
        db.query(OktaUser)
        .options(
            selectinload(OktaUser.active_group_memberships).options(*user_group_member_options()),
            selectinload(OktaUser.active_group_ownerships).options(*user_group_member_options()),
            selectinload(OktaUser.active_group_memberships_and_ownerships).options(*user_group_member_options()),
            selectinload(OktaUser.all_group_memberships_and_ownerships).options(*user_group_member_options()),
            joinedload(OktaUser.manager),
        )
        .filter(_db.or_(OktaUser.id == user_id, OktaUser.email.ilike(user_id)))
        .order_by(nullsfirst(OktaUser.deleted_at.desc()))
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return dump_orm(_user_adapter, user)


@router.get("/{user_id}/audit", name="user_audit_by_id")
def get_user_audit(user_id: str, request: Request, current_user_id: CurrentUserId) -> RedirectResponse:
    qp = dict(request.query_params)
    qp["user_id"] = user_id
    from urllib.parse import urlencode

    return RedirectResponse(url=f"/api/audit/users?{urlencode(qp)}", status_code=307)
