"""MCP tool registrations.

Each tool mirrors the corresponding REST endpoint:

  - Reuses the same eager-load shape so ``lazy="raise_on_sql"`` doesn't
    bite at runtime (every relationship the response schema reads must
    be loaded before serialization).
  - Reuses the same Pydantic response model so the wire shape matches
    what the REST API returns. This keeps the LLM client's mental model
    consistent across REST and MCP.
  - Re-checks authorization explicitly via the bare predicates in
    ``api.auth.permissions`` — the FastAPI ``Depends(require_*)``
    pattern doesn't propagate into a Starlette Route, and the MCP
    handler runs outside FastAPI's DI in any case. The per-tool
    authorization shape depends on the REST endpoint being mirrored:

      - Read tools: every GET endpoint in the REST API is gated only on
        ``require_authenticated``. The MCP equivalent is
        ``@requires_scope(MCP_SCOPE_READ_ALL)`` — authentication is
        already enforced by the ASGI middleware, the scope check is the
        only extra gate. No bare-predicate call needed.
      - Write tools: the authorization gate is the **same predicate
        the REST handler runs**. ``create_access_request`` matches
        ``POST /api/requests`` which is open to any authenticated user
        for self-targeted requests, so the scope check + an active
        ``OktaUser`` lookup is enough. ``create_role_request`` mirrors
        ``POST /api/role-requests``, which gates on
        ``can_manage_group(role)`` — the tool calls that predicate
        directly. ``create_group_request`` mirrors
        ``POST /api/group-requests``, which is open to any
        authenticated user.

    When adding a future tool that mirrors an admin- or owner-gated
    REST endpoint, call the corresponding ``is_*`` / ``can_*``
    function from ``api/auth/permissions.py`` inline at the top of the
    handler. Returning an ``{"error": "..."}`` envelope with the same
    message the REST handler would have raised keeps the contract
    parallel.
  - Goes through the same operation class for writes — so audit logs,
    plugin hooks, and conditional-access flows fire identically.

Returns are JSON strings (the MCP tool result wire shape). Errors are
returned as ``{"error": "..."}`` strings rather than raised — FastMCP's
exception-to-tool-error path is non-deterministic across versions and a
JSON string keeps the error contract stable.

Tool handlers are sync (``def``, not ``async def``). FastMCP runs them
on its threadpool, the same way FastAPI does for sync route handlers,
which keeps the sync SQLAlchemy ORM happy. Do not change tools to async
without first porting the ORM.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

from mcp.types import ToolAnnotations
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, nullsfirst
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.auth.permissions import can_manage_group
from api.context import get_request_context
from api.extensions import db as _db_shim
from api.mcp.auth import (
    MCP_SCOPE_CREATE_REQUESTS,
    MCP_SCOPE_READ_ALL,
    get_mcp_user_id,
    requires_scope,
)
from api.mcp.db import mcp_db_session
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    AppTagMap,
    GroupRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleRequest,
    Tag,
)
from api.routers._eager import (
    group_tag_map_options,
    polymorphic_group_options,
    role_group_map_options,
    user_group_member_options,
)
from api.schemas import (
    AccessRequestDetail,
    AccessRequestSummary,
    AppDetail,
    AppSummary,
    CreateAccessRequestBody,
    CreateGroupRequestBody,
    CreateRoleRequestBody,
    GroupDetail,
    GroupMembersSummary,
    GroupRequestDetail,
    GroupSummary,
    OktaUserDetail,
    OktaUserSummary,
    RoleGroupListItem,
    RoleRequestDetail,
    RoleRequestSummary,
    TagDetail,
    TagListItem,
)
from api.schemas.requests_schemas import _AppGroupRequestBody

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Per-tool pagination cap. Smaller than the REST cap (MAX_PER_PAGE=1000)
# because tool outputs go into an LLM context window — 100 rows is more
# than enough to answer most questions and keeps token cost predictable.
MCP_MAX_PAGE_SIZE = 100
MCP_DEFAULT_PAGE_SIZE = 50


# Tool annotations. These are advisory hints — compliant MCP clients use
# them to decide whether to auto-allow a call in modes like Claude Code's
# `--dangerously-skip-permissions`. They do not enforce anything on the
# server side and a non-compliant client can ignore them. The structural
# defense remains: writes only file PENDING proposals, and approval is
# never exposed via MCP.
#
# Reads: pure DB reads of Access state. openWorldHint=False because the
# tool's domain of interaction is bounded (Access's DB).
_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    openWorldHint=False,
)
# Writes: file PENDING request rows. destructiveHint=False is the honest
# framing — these tools do not effect a state change on their own; a
# human still has to approve through a different surface. Not idempotent
# (each call creates a fresh request record and emits an audit log entry,
# even though the supersede-prior-pending logic ends older duplicates).
_WRITE_PROPOSAL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


# Cached TypeAdapter for polymorphic GroupDetail — the discriminated union
# doesn't expose ``model_validate`` directly. One adapter is enough; building
# the adapter is non-trivial so we only pay for it once.
_group_adapter: TypeAdapter[Any] = TypeAdapter(GroupDetail)


def _error(message: str) -> str:
    """Build the canonical JSON error envelope. Tool handlers return this
    as a string rather than raising so the contract is stable across
    FastMCP versions and across clients."""
    return json.dumps({"error": message})


def _validation_error(exc: ValidationError) -> str:
    """Friendly first-error projection for ``CreateAccessRequestBody``
    parsing failures."""
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"]) or "input"
    return _error(f"{loc}: {err['msg']}")


def _clamp_pagination(page: int, size: int) -> tuple[int, int, Optional[str]]:
    """Validate the (page, size) pair an MCP client sent. Returns the
    sanitized pair plus an optional error string."""
    if page < 0:
        return page, size, "page must be >= 0"
    if size < 1:
        return page, size, "size must be >= 1"
    if size > MCP_MAX_PAGE_SIZE:
        return page, MCP_MAX_PAGE_SIZE, None  # silent clamp
    return page, size, None


def _paginate_query(query: Any, page: int, size: int) -> dict[str, Any]:
    """Run ``query`` and return the standard envelope. ``page`` is
    zero-indexed."""
    total = query.order_by(None).count()
    pages = max(1, (total + size - 1) // size)
    items = query.limit(size).offset(page * size).all()
    return {"total": total, "pages": pages, "page": page, "size": size, "items": items}


def _envelope(*, total: int, pages: int, page: int, size: int, results: list[Any]) -> dict[str, Any]:
    return {
        "total": total,
        "pages": pages,
        "page": page,
        "size": size,
        "has_next": page < pages - 1,
        "has_prev": page > 0,
        "results": results,
    }


def _serialize_model(model: Any) -> Any:
    """Dump a Pydantic model to a plain JSON-serializable dict."""
    return model.model_dump(mode="json")


def _group_load_options() -> tuple:
    """Match ``api.routers.groups.DEFAULT_LOAD_OPTIONS`` for the detail
    shape. Inlined rather than imported so the tools module doesn't pull
    in the entire groups router at import time."""
    return (
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        selectinload(OktaGroup.active_user_memberships).options(*user_group_member_options()),
        selectinload(OktaGroup.active_user_ownerships).options(*user_group_member_options()),
        selectinload(OktaGroup.active_role_member_mappings).options(*role_group_map_options()),
        selectinload(OktaGroup.active_role_owner_mappings).options(*role_group_map_options()),
        selectinload(RoleGroup.active_role_associated_group_member_mappings).options(*role_group_map_options()),
        selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(*role_group_map_options()),
        joinedload(AppGroup.app),
        selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
    )


def _app_load_options() -> tuple:
    """Match ``api.routers.apps.APP_LOAD_OPTIONS``."""
    inner = (
        selectinload(AppGroup.active_user_memberships).options(*user_group_member_options()),
        selectinload(AppGroup.active_user_ownerships).options(*user_group_member_options()),
    )
    return (
        selectinload(App.active_app_tags).options(
            joinedload(AppTagMap.active_tag),
            joinedload(AppTagMap.active_app),
        ),
        selectinload(App.active_owner_app_groups).options(*inner),
        selectinload(App.active_non_owner_app_groups).options(*inner),
    )


def _access_request_detail_load_options() -> tuple:
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


def _access_request_summary_load_options() -> tuple:
    return (
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AccessRequest.requester),
        joinedload(AccessRequest.active_requester),
        selectinload(AccessRequest.requested_group),
        selectinload(AccessRequest.active_requested_group),
        joinedload(AccessRequest.resolver),
        joinedload(AccessRequest.active_resolver),
    )


def _role_request_summary_load_options() -> tuple:
    return (
        joinedload(RoleRequest.requester),
        joinedload(RoleRequest.resolver),
        joinedload(RoleRequest.active_resolver),
        selectinload(RoleRequest.requester_role).options(*polymorphic_group_options()),
        selectinload(RoleRequest.active_requester_role).options(*polymorphic_group_options()),
        selectinload(RoleRequest.requested_group).options(*polymorphic_group_options()),
        selectinload(RoleRequest.active_requested_group).options(*polymorphic_group_options()),
    )


def _role_request_detail_load_options() -> tuple:
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


def _group_request_load_options() -> tuple:
    return (
        joinedload(GroupRequest.requester),
        joinedload(GroupRequest.active_requester),
        joinedload(GroupRequest.resolver),
        joinedload(GroupRequest.active_resolver),
        joinedload(GroupRequest.approved_group),
    )


def _tag_load_options() -> tuple:
    return (
        selectinload(Tag.active_group_tags).options(*group_tag_map_options()),
        selectinload(Tag.active_app_tags).options(
            joinedload(AppTagMap.active_app),
            joinedload(AppTagMap.active_tag),
        ),
    )


# ---------------------------------------------------------------------------
# Tool registrations
# ---------------------------------------------------------------------------


def register_tools(mcp: "FastMCP") -> None:
    """Register every v1 tool on the given FastMCP instance.

    Pattern:
      - Each handler carries ``@requires_scope(MCP_SCOPE_*)`` between
        ``@mcp.tool(...)`` and the function definition — enforced
        regardless of which auth provider resolved the request.
      - ``get_mcp_user_id()`` returns the authenticated user id.
      - Reads use ``_db_shim.session`` directly (no commit).
      - Writes use ``with mcp_db_session() as session:`` (commits on
        success).
      - Output is JSON. Errors are ``{"error": "..."}`` strings.
    """

    _register_group_tools(mcp)
    _register_role_tools(mcp)
    _register_app_tools(mcp)
    _register_user_tools(mcp)
    _register_tag_tools(mcp)
    _register_access_request_tools(mcp)
    _register_role_request_tools(mcp)
    _register_group_request_tools(mcp)
    _register_audit_tool(mcp)
    _register_write_tools(mcp)


# --- Groups -----------------------------------------------------------------


def _register_group_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_groups",
        title="List groups",
        description=(
            "List groups (OktaGroup, AppGroup, RoleGroup). Returns a paginated "
            "list of group summaries. Use 'q' for free-text search across name "
            "and description. Set 'managed=false' to include externally-managed "
            "groups. Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_groups(
        q: str = "", managed: Optional[bool] = None, page: int = 0, size: int = MCP_DEFAULT_PAGE_SIZE
    ) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        query = (
            db.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                selectinload(OktaGroup.active_group_tags).options(*group_tag_map_options()),
                selectinload(RoleGroup.active_role_associated_group_member_mappings).options(*role_group_map_options()),
                selectinload(RoleGroup.active_role_associated_group_owner_mappings).options(*role_group_map_options()),
                joinedload(AppGroup.app),
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .order_by(func.lower(OktaGroup.name))
        )
        if q:
            like = f"%{q}%"
            query = query.filter(_db_shim.or_(OktaGroup.name.ilike(like), OktaGroup.description.ilike(like)))
        if managed is not None:
            query = query.filter(OktaGroup.is_managed == managed)

        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(GroupSummary)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(
                total=page_data["total"],
                pages=page_data["pages"],
                page=page,
                size=size,
                results=results,
            )
        )

    @mcp.tool(
        name="get_group",
        title="Get group",
        description=(
            "Get full detail for a single group by id or by name. The 'active_' "
            "prefix on a list field means 'currently in force' (ended_at is "
            "null or future). Key response fields: "
            "'active_user_memberships' = users who are direct members of this "
            "group; "
            "'active_user_ownerships' = users who directly manage this group; "
            "'active_role_member_mappings' = roles whose members get membership "
            "in THIS group; "
            "'active_role_owner_mappings' = roles whose members get ownership "
            "of THIS group; "
            "'active_group_tags' = enabled tags on this group (carry "
            "constraints like time limits and reason requirements). For "
            "AppGroups, 'app' identifies the parent app. Requires the "
            "'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_group(group_id_or_name: str) -> str:
        db = _db_shim.session
        group = (
            db.query(OktaGroup)
            .options(*_group_load_options())
            .filter(_db_shim.or_(OktaGroup.id == group_id_or_name, OktaGroup.name == group_id_or_name))
            .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
            .first()
        )
        if group is None:
            return _error("Group not found")
        validated = _group_adapter.validate_python(group, from_attributes=True)
        return _group_adapter.dump_json(validated).decode()

    @mcp.tool(
        name="list_group_memberships",
        title="List group memberships",
        description=(
            "List active members and owners of a group by id or name. Returns "
            "two lists: 'members' (user ids who are members) and 'owners' "
            "(user ids who are owners). Use 'get_user' for full user detail. "
            "Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_group_memberships(group_id_or_name: str) -> str:
        db = _db_shim.session
        group = (
            db.query(OktaGroup)
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(_db_shim.or_(OktaGroup.id == group_id_or_name, OktaGroup.name == group_id_or_name))
            .first()
        )
        if group is None:
            return _error("Group not found")
        rows = (
            db.query(OktaUserGroupMember)
            .with_entities(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
            .filter(
                _db_shim.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > _db_shim.func.now(),
                )
            )
            .filter(OktaUserGroupMember.group_id == group.id)
            .group_by(OktaUserGroupMember.user_id, OktaUserGroupMember.is_owner)
            .all()
        )
        result = GroupMembersSummary(
            members=[r.user_id for r in rows if not r.is_owner],
            owners=[r.user_id for r in rows if r.is_owner],
        )
        return result.model_dump_json()


# --- Roles ------------------------------------------------------------------


def _register_role_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_roles",
        title="List roles",
        description=(
            "List Role groups (job-function-shaped groups, prefix configurable). "
            "Returns a paginated list of role summaries. Use 'owner_id' (user id "
            "or email) to filter to roles a specific user owns. Requires the "
            "'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_roles(q: str = "", owner_id: str = "", page: int = 0, size: int = MCP_DEFAULT_PAGE_SIZE) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        query = db.query(RoleGroup).filter(RoleGroup.deleted_at.is_(None)).order_by(func.lower(RoleGroup.name))
        if owner_id:
            owner = (
                db.query(OktaUser)
                .filter(_db_shim.or_(OktaUser.id == owner_id, OktaUser.email.ilike(owner_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first()
            )
            if owner is None:
                return _error(f"Owner not found: {owner_id}")
            owned_role_ids = [
                row.group_id
                for row in db.query(OktaUserGroupMember.group_id)
                .filter(OktaUserGroupMember.user_id == owner.id)
                .filter(OktaUserGroupMember.is_owner.is_(True))
                .filter(
                    _db_shim.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > _db_shim.func.now(),
                    )
                )
                .all()
            ]
            query = query.filter(RoleGroup.id.in_(owned_role_ids))
        if q:
            like = f"%{q}%"
            query = query.filter(_db_shim.or_(RoleGroup.name.ilike(like), RoleGroup.description.ilike(like)))
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(RoleGroupListItem)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_role",
        title="Get role",
        description=(
            "Get full detail for a single role by id or by name. A role IS a "
            "group (single-table inheritance), so it shares the group fields "
            "below — but with role-specific mappings instead of the inbound "
            "ones. The 'active_' prefix means 'currently in force'. Key "
            "response fields: "
            "'active_user_memberships' = users who are members of this role; "
            "'active_user_ownerships' = users who manage this role; "
            "'active_role_associated_group_member_mappings' = groups this role "
            "grants its members MEMBERSHIP in (the access the role confers); "
            "'active_role_associated_group_owner_mappings' = groups this role "
            "grants its members OWNERSHIP of; "
            "'active_group_tags' = enabled tags on the role itself. Requires "
            "the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_role(role_id_or_name: str) -> str:
        db = _db_shim.session
        role = (
            db.query(RoleGroup)
            .options(*_group_load_options())
            .filter(_db_shim.or_(RoleGroup.id == role_id_or_name, RoleGroup.name == role_id_or_name))
            .order_by(nullsfirst(RoleGroup.deleted_at.desc()))
            .first()
        )
        if role is None:
            return _error("Role not found")
        return _group_adapter.dump_json(_group_adapter.validate_python(role, from_attributes=True)).decode()


# --- Apps -------------------------------------------------------------------


def _register_app_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_apps",
        title="List apps",
        description=(
            "List Access apps. An 'app' in Access is a logical grouping of "
            "AppGroups in Access's DB only — it does not correspond to an Okta "
            "app. Use 'q' for free-text search across name and description. "
            "Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_apps(q: str = "", page: int = 0, size: int = MCP_DEFAULT_PAGE_SIZE) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        query = db.query(App).filter(App.deleted_at.is_(None)).order_by(func.lower(App.name))
        if q:
            like = f"%{q}%"
            query = query.filter(_db_shim.or_(App.name.ilike(like), App.description.ilike(like)))
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(AppSummary)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_app",
        title="Get app",
        description=(
            "Get full detail for a single app by id or by name. The 'active_' "
            "prefix means 'currently in force'. Key response fields: "
            "'active_owner_app_groups' = the app's owner AppGroup(s); members "
            "of these groups are the app owners and are an approval-routing "
            "tier above Access admins; "
            "'active_non_owner_app_groups' = the app's regular AppGroups "
            "(grant access to the app); "
            "'active_app_tags' = enabled tags on the app (constraints "
            "propagate to all of the app's AppGroups). Requires the "
            "'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_app(app_id_or_name: str) -> str:
        db = _db_shim.session
        app = (
            db.query(App)
            .options(*_app_load_options())
            .filter(App.deleted_at.is_(None))
            .filter(_db_shim.or_(App.id == app_id_or_name, App.name == app_id_or_name))
            .first()
        )
        if app is None:
            return _error("App not found")
        return AppDetail.model_validate(app, from_attributes=True).model_dump_json()


# --- Users ------------------------------------------------------------------


def _register_user_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_users",
        title="List users",
        description=(
            "List active Okta users. Use 'q' for free-text search across email, "
            "first/last name, display name, and custom profile attributes. "
            "Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_users(q: str = "", page: int = 0, size: int = MCP_DEFAULT_PAGE_SIZE) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        query = db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).order_by(func.lower(OktaUser.email))
        if q:
            like = f"%{q}%"
            query = query.filter(
                _db_shim.or_(
                    OktaUser.email.ilike(like),
                    OktaUser.first_name.ilike(like),
                    OktaUser.last_name.ilike(like),
                    OktaUser.display_name.ilike(like),
                    (OktaUser.first_name + " " + OktaUser.last_name).ilike(like),
                )
            )
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(OktaUserSummary)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_user",
        title="Get user",
        description=(
            "Get detail for a single user by id, email, or the literal '@me' to "
            "look up the calling user. The 'active_' prefix means 'currently in "
            "force'. Key response fields: "
            "'active_group_memberships' = groups (including roles) this user "
            "is currently a member of, either directly or via a role; "
            "'active_group_ownerships' = groups this user currently manages. "
            "Each row carries 'active_role_group_mapping' which is non-null "
            "when the membership was granted via a role (and identifies which "
            "one), null when it's a direct grant. Requires the 'read_all' "
            "scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_user(user_id_or_email: str) -> str:
        if user_id_or_email == "@me":
            user_id_or_email = get_mcp_user_id()
        db = _db_shim.session
        user = (
            db.query(OktaUser)
            .options(
                selectinload(OktaUser.active_group_memberships).options(*user_group_member_options()),
                selectinload(OktaUser.active_group_ownerships).options(*user_group_member_options()),
                joinedload(OktaUser.manager),
            )
            .filter(_db_shim.or_(OktaUser.id == user_id_or_email, OktaUser.email.ilike(user_id_or_email)))
            .order_by(nullsfirst(OktaUser.deleted_at.desc()))
            .first()
        )
        if user is None:
            return _error("User not found")
        return OktaUserDetail.model_validate(user, from_attributes=True).model_dump_json()


# --- Tags -------------------------------------------------------------------


def _register_tag_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_tags",
        title="List tags",
        description=(
            "List tags. Tags carry constraints (member/owner time limits, "
            "reason requirements, self-add restrictions) that apply to the "
            "groups/apps they're attached to. Only ENABLED tags actually "
            "enforce — but list_tags returns all tags including disabled ones. "
            "Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_tags(q: str = "", page: int = 0, size: int = MCP_DEFAULT_PAGE_SIZE) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        query = db.query(Tag).filter(Tag.deleted_at.is_(None)).order_by(func.lower(Tag.name))
        if q:
            like = f"%{q}%"
            query = query.filter(_db_shim.or_(Tag.name.ilike(like), Tag.description.ilike(like)))
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(TagListItem)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_tag",
        title="Get tag",
        description=(
            "Get detail for a single tag by id or name. Returns the constraints "
            "dict, the enabled flag, and the groups/apps the tag is currently "
            "attached to. Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_tag(tag_id_or_name: str) -> str:
        db = _db_shim.session
        tag = (
            db.query(Tag)
            .options(*_tag_load_options())
            .filter(_db_shim.or_(Tag.id == tag_id_or_name, Tag.name == tag_id_or_name))
            .order_by(nullsfirst(Tag.deleted_at.desc()))
            .first()
        )
        if tag is None:
            return _error("Tag not found")
        return TagDetail.model_validate(tag, from_attributes=True).model_dump_json()


# --- Access requests --------------------------------------------------------


def _register_access_request_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_access_requests",
        title="List access requests",
        description=(
            "List access requests (a user requesting membership/ownership in a "
            "group/role). Filter by 'status' (PENDING/APPROVED/REJECTED), "
            "'requester_user_id' (use '@me' for self), 'requested_group_id', or "
            "'resolver_user_id'. Use 'q' for free-text search. Requires the "
            "'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_access_requests(
        q: str = "",
        status: str = "",
        requester_user_id: str = "",
        requested_group_id: str = "",
        resolver_user_id: str = "",
        page: int = 0,
        size: int = MCP_DEFAULT_PAGE_SIZE,
    ) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        current_user_id = get_mcp_user_id()
        query = (
            db.query(AccessRequest)
            .options(*_access_request_summary_load_options())
            .order_by(AccessRequest.created_at.desc())
        )
        if status:
            query = query.filter(AccessRequest.status == status)
        if requester_user_id:
            if requester_user_id == "@me":
                query = query.filter(AccessRequest.requester_user_id == current_user_id)
            else:
                query = query.filter(AccessRequest.requester_user_id == requester_user_id)
        if requested_group_id:
            query = query.filter(AccessRequest.requested_group_id == requested_group_id)
        if resolver_user_id:
            if resolver_user_id == "@me":
                query = query.filter(AccessRequest.resolver_user_id == current_user_id)
            else:
                query = query.filter(AccessRequest.resolver_user_id == resolver_user_id)
        if q:
            like = f"%{q}%"
            query = query.filter(
                _db_shim.or_(
                    AccessRequest.id.like(f"{q}%"),
                    AccessRequest.request_reason.ilike(like),
                )
            )
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(AccessRequestSummary)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_access_request",
        title="Get access request",
        description=(
            "Get detail for a single access request by id. Includes the rich "
            "'requested_group' projection (group tags, role-association "
            "mappings on role groups, app/is_owner on app groups). Requires the "
            "'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_access_request(access_request_id: str) -> str:
        db = _db_shim.session
        ar = (
            db.query(AccessRequest)
            .options(*_access_request_detail_load_options())
            .filter(AccessRequest.id == access_request_id)
            .first()
        )
        if ar is None:
            return _error("Access request not found")
        return AccessRequestDetail.model_validate(ar, from_attributes=True).model_dump_json()


# --- Role requests ----------------------------------------------------------


def _register_role_request_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_role_requests",
        title="List role requests",
        description=(
            "List role requests (a role owner requesting that the role be "
            "granted access to a group). Filter by 'status', "
            "'requester_user_id' (use '@me'), 'requester_role_id', "
            "'requested_group_id'. Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_role_requests(
        q: str = "",
        status: str = "",
        requester_user_id: str = "",
        requester_role_id: str = "",
        requested_group_id: str = "",
        page: int = 0,
        size: int = MCP_DEFAULT_PAGE_SIZE,
    ) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        current_user_id = get_mcp_user_id()
        query = (
            db.query(RoleRequest).options(*_role_request_summary_load_options()).order_by(RoleRequest.created_at.desc())
        )
        if status:
            query = query.filter(RoleRequest.status == status)
        if requester_user_id:
            if requester_user_id == "@me":
                query = query.filter(RoleRequest.requester_user_id == current_user_id)
            else:
                query = query.filter(RoleRequest.requester_user_id == requester_user_id)
        if requester_role_id:
            query = query.filter(RoleRequest.requester_role_id == requester_role_id)
        if requested_group_id:
            query = query.filter(RoleRequest.requested_group_id == requested_group_id)
        if q:
            like = f"%{q}%"
            query = query.filter(_db_shim.or_(RoleRequest.id.like(f"{q}%"), RoleRequest.request_reason.ilike(like)))
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(RoleRequestSummary)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_role_request",
        title="Get role request",
        description=(
            "Get detail for a single role request by id. Includes the rich "
            "'requester_role' projection (role members) and the target group's "
            "active tags. Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_role_request(role_request_id: str) -> str:
        db = _db_shim.session
        rr = (
            db.query(RoleRequest)
            .options(*_role_request_detail_load_options())
            .filter(RoleRequest.id == role_request_id)
            .first()
        )
        if rr is None:
            return _error("Role request not found")
        return RoleRequestDetail.model_validate(rr, from_attributes=True).model_dump_json()


# --- Group requests ---------------------------------------------------------


def _register_group_request_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_group_requests",
        title="List group requests",
        description=(
            "List group requests (a user requesting creation of a new group, "
            "role, or app group). Filter by 'status', 'requester_user_id' (use "
            "'@me'), 'requested_group_type' (okta_group/role_group/app_group), "
            "'requested_app_id'. Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_group_requests(
        q: str = "",
        status: str = "",
        requester_user_id: str = "",
        requested_group_type: str = "",
        requested_app_id: str = "",
        page: int = 0,
        size: int = MCP_DEFAULT_PAGE_SIZE,
    ) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        current_user_id = get_mcp_user_id()
        query = db.query(GroupRequest).options(*_group_request_load_options()).order_by(GroupRequest.created_at.desc())
        if status:
            query = query.filter(GroupRequest.status == status)
        if requester_user_id:
            if requester_user_id == "@me":
                query = query.filter(GroupRequest.requester_user_id == current_user_id)
            else:
                query = query.filter(GroupRequest.requester_user_id == requester_user_id)
        if requested_group_type:
            query = query.filter(GroupRequest.requested_group_type == requested_group_type)
        if requested_app_id:
            query = query.filter(GroupRequest.requested_app_id == requested_app_id)
        if q:
            like = f"%{q}%"
            query = query.filter(
                _db_shim.or_(
                    GroupRequest.id.like(f"{q}%"),
                    GroupRequest.requested_group_name.ilike(like),
                    GroupRequest.request_reason.ilike(like),
                )
            )
        page_data = _paginate_query(query, page, size)
        adapter: TypeAdapter[Any] = TypeAdapter(GroupRequestDetail)
        results = [_serialize_model(adapter.validate_python(row, from_attributes=True)) for row in page_data["items"]]
        return json.dumps(
            _envelope(total=page_data["total"], pages=page_data["pages"], page=page, size=size, results=results)
        )

    @mcp.tool(
        name="get_group_request",
        title="Get group request",
        description=("Get detail for a single group request by id. Requires the " "'read_all' scope."),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def get_group_request(group_request_id: str) -> str:
        db = _db_shim.session
        gr = (
            db.query(GroupRequest)
            .options(*_group_request_load_options())
            .filter(GroupRequest.id == group_request_id)
            .first()
        )
        if gr is None:
            return _error("Group request not found")
        return GroupRequestDetail.model_validate(gr, from_attributes=True).model_dump_json()


# --- Audit ------------------------------------------------------------------


def _register_audit_tool(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="list_audit_entries",
        title="List access history",
        description=(
            "List user-group membership audit rows (the OktaUserGroupMember "
            "table). Each row is a single membership/ownership grant or "
            "revocation. Filter by 'user_id' (use '@me'), 'group_id', or "
            "'is_owner' (true/false). Set 'active_only=true' (default) to see "
            "only currently-active records; false includes ended ones. "
            "Requires the 'read_all' scope."
        ),
        annotations=_READ_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_READ_ALL)
    def list_audit_entries(
        user_id: str = "",
        group_id: str = "",
        is_owner: Optional[bool] = None,
        active_only: bool = True,
        page: int = 0,
        size: int = MCP_DEFAULT_PAGE_SIZE,
    ) -> str:
        page, size, err = _clamp_pagination(page, size)
        if err:
            return _error(err)
        db = _db_shim.session
        current_user_id = get_mcp_user_id()

        query = db.query(OktaUserGroupMember).order_by(OktaUserGroupMember.created_at.desc())

        if user_id:
            resolved_user_id = current_user_id if user_id == "@me" else user_id
            # Accept email too — match by id or email.
            user = (
                db.query(OktaUser)
                .filter(_db_shim.or_(OktaUser.id == resolved_user_id, OktaUser.email.ilike(resolved_user_id)))
                .order_by(nullsfirst(OktaUser.deleted_at.desc()))
                .first()
            )
            if user is None:
                return _error(f"User not found: {user_id}")
            query = query.filter(OktaUserGroupMember.user_id == user.id)

        if group_id:
            group = (
                db.query(OktaGroup)
                .filter(_db_shim.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
                .order_by(nullsfirst(OktaGroup.deleted_at.desc()))
                .first()
            )
            if group is None:
                return _error(f"Group not found: {group_id}")
            query = query.filter(OktaUserGroupMember.group_id == group.id)

        if is_owner is not None:
            query = query.filter(OktaUserGroupMember.is_owner == is_owner)

        if active_only:
            query = query.filter(
                _db_shim.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > _db_shim.func.now(),
                )
            )

        # Compact, dict-shaped rows: the rich AuditUserGroupRow is built
        # by hand in api/routers/audit.py with eager-loads on every
        # related model. For MCP we keep it simple and return only the
        # scalar columns + user_id / group_id; clients that need richer
        # data can call get_user / get_group on the id values.
        total = query.order_by(None).count()
        pages = max(1, (total + size - 1) // size)
        items = query.limit(size).offset(page * size).all()
        results = [
            {
                "id": m.id,
                "user_id": m.user_id,
                "group_id": m.group_id,
                "role_group_map_id": getattr(m, "role_group_map_id", None),
                "is_owner": m.is_owner,
                "should_expire": getattr(m, "should_expire", None),
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "ended_at": m.ended_at.isoformat() if m.ended_at else None,
                "created_actor_id": getattr(m, "created_actor_id", None),
                "ended_actor_id": getattr(m, "ended_actor_id", None),
                "created_reason": m.created_reason or "",
                "access_request_id": getattr(m, "access_request_id", None),
            }
            for m in items
        ]
        return json.dumps(_envelope(total=total, pages=pages, page=page, size=size, results=results))


# --- Write tools ------------------------------------------------------------


def _register_write_tools(mcp: "FastMCP") -> None:
    @mcp.tool(
        name="create_access_request",
        title="Submit access request",
        description=(
            "Submit an access request for the calling user — a request for "
            "membership (default) or ownership ('group_owner=true') in a "
            "specific group or role. The request is created in PENDING state; "
            "approval is handled out-of-band and is NOT exposed via MCP. "
            "'ending_at' is an RFC822 timestamp string (e.g. 'Mon, 12 Feb 2026 "
            "12:00:00 +0000') and is strongly recommended for non-day-to-day "
            "access — leave unset only for indefinite, ongoing access. Tag "
            "constraints on the target may force a reason or a time limit; "
            "use get_group/get_role first to see what's required. Requires "
            "the 'create_requests' scope."
        ),
        annotations=_WRITE_PROPOSAL_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_CREATE_REQUESTS)
    def create_access_request(
        group_id: str,
        reason: str = "",
        group_owner: bool = False,
        ending_at: str = "",
    ) -> str:
        current_user_id = get_mcp_user_id()

        # Validate the body through the same Pydantic model the REST
        # endpoint uses. Field-level errors come back as 'group_id:
        # ...'. Empty `ending_at` means indefinite — pass through.
        body_dict: dict[str, Any] = {"group_id": group_id, "group_owner": group_owner, "reason": reason}
        if ending_at:
            body_dict["ending_at"] = ending_at
        try:
            body = CreateAccessRequestBody.model_validate(body_dict)
        except ValidationError as exc:
            return _validation_error(exc)

        # Local import so the operation isn't loaded into the module
        # graph until a write tool actually fires. Reads don't need it.
        from api.operations import CreateAccessRequest, RejectAccessRequest

        with mcp_db_session() as db:
            requester = (
                db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first()
            )
            if requester is None:
                # No active OktaUser for the resolved id — typically a
                # service-token request hitting an action that requires
                # a real human identity. The REST handler returns 403
                # here; we mirror that.
                return _error("Current user is not allowed to perform this action")
            group = (
                db.query(OktaGroup).filter(OktaGroup.id == body.group_id).filter(OktaGroup.deleted_at.is_(None)).first()
            )
            if group is None:
                return _error("Group not found")
            if not group.is_managed:
                return _error("Groups not managed by Access cannot be modified")

            # Same supersede-prior-pending behavior as POST /api/requests.
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

            # Execute the operation. Audit-log emission, conditional
            # access hooks, and notification hooks all fire from inside
            # `execute()` — the per-operation contract handles the rest.
            ar = CreateAccessRequest(
                requester_user=requester,
                requested_group=group,
                request_ownership=body.group_owner,
                request_reason=body.reason or "",
                request_ending_at=body.ending_at,
            ).execute()
            if ar is None:
                return _error("Access request could not be created")

            refreshed = (
                db.query(AccessRequest)
                .options(*_access_request_summary_load_options())
                .filter(AccessRequest.id == ar.id)
                .first()
            )
            # Log the MCP-origin tag onto the audit channel separately
            # from the operation's own audit emission, which already
            # picks up `source="mcp"` from the active RequestContext.
            _ctx = get_request_context()
            if _ctx is not None and _ctx.source == "mcp":
                logger.info(
                    f"MCP create_access_request id={ar.id} requester={current_user_id} "
                    f"group={group.id} ownership={body.group_owner}"
                )
            return AccessRequestSummary.model_validate(refreshed, from_attributes=True).model_dump_json()

    @mcp.tool(
        name="create_role_request",
        title="Submit role request",
        description=(
            "Submit a role request — a request to grant a ROLE access to a "
            "specific group (or app group). Only role OWNERS (and Access "
            "admins) can submit role requests for a given role; if you are "
            "not an owner of the role, this tool will return a 403-equivalent "
            "error. The target must NOT itself be a role (roles cannot "
            "contain other roles). 'group_owner=true' requests that the role "
            "be granted ownership of the target rather than membership. The "
            "request is created PENDING; approval is out-of-band and not "
            "exposed via MCP. Requires the 'create_requests' scope."
        ),
        annotations=_WRITE_PROPOSAL_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_CREATE_REQUESTS)
    def create_role_request(
        role_id: str,
        group_id: str,
        reason: str = "",
        group_owner: bool = False,
        ending_at: str = "",
    ) -> str:
        current_user_id = get_mcp_user_id()

        body_dict: dict[str, Any] = {
            "role_id": role_id,
            "group_id": group_id,
            "group_owner": group_owner,
            "reason": reason,
        }
        if ending_at:
            body_dict["ending_at"] = ending_at
        try:
            body = CreateRoleRequestBody.model_validate(body_dict)
        except ValidationError as exc:
            return _validation_error(exc)

        from api.operations import CreateRoleRequest, RejectRoleRequest

        with mcp_db_session() as db:
            requester = (
                db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first()
            )
            role = (
                db.query(RoleGroup).filter(RoleGroup.deleted_at.is_(None)).filter(RoleGroup.id == body.role_id).first()
            )
            if role is None:
                return _error("Role not found")
            # Same authorization gate as POST /api/role-requests: the
            # requester must be able to manage the role (own it directly,
            # own the role's app — n/a for roles — or be an Access admin).
            # If the requester has no active OktaUser row at all (e.g.
            # service-token traffic), can_manage_group returns False and
            # we surface the same 403 message the REST handler does.
            if requester is None or not can_manage_group(db, current_user_id, role):
                return _error("Current user is not allowed to perform this action")
            group = (
                db.query(OktaGroup).filter(OktaGroup.deleted_at.is_(None)).filter(OktaGroup.id == body.group_id).first()
            )
            if group is None:
                return _error("Group not found")
            if not group.is_managed:
                return _error("Groups not managed by Access cannot be modified")
            if type(group) is RoleGroup:
                # A role cannot contain other roles — enforced at the
                # model level for direct adds, and mirrored here so the
                # error surfaces before we hit the operation.
                return _error("Role requests may only be made for groups and app groups (not roles).")

            existing = (
                db.query(RoleRequest)
                .filter(RoleRequest.requester_user_id == current_user_id)
                .filter(RoleRequest.requester_role_id == body.role_id)
                .filter(RoleRequest.requested_group_id == body.group_id)
                .filter(RoleRequest.request_ownership == body.group_owner)
                .filter(RoleRequest.status == AccessRequestStatus.PENDING)
                .filter(RoleRequest.resolved_at.is_(None))
                .all()
            )
            for old in existing:
                RejectRoleRequest(
                    role_request=old,
                    rejection_reason="Closed due to duplicate role request creation",
                    notify_requester=False,
                    current_user_id=current_user_id,
                ).execute()

            rr = CreateRoleRequest(
                requester_user=requester,
                requester_role=role,
                requested_group=group,
                request_ownership=body.group_owner,
                request_reason=body.reason or "",
                request_ending_at=body.ending_at,
            ).execute()
            if rr is None:
                return _error("Role request could not be created")
            refreshed = (
                db.query(RoleRequest)
                .options(*_role_request_summary_load_options())
                .filter(RoleRequest.id == rr.id)
                .first()
            )
            _ctx = get_request_context()
            if _ctx is not None and _ctx.source == "mcp":
                logger.info(
                    f"MCP create_role_request id={rr.id} requester={current_user_id} "
                    f"role={role.id} group={group.id} ownership={body.group_owner}"
                )
            return RoleRequestSummary.model_validate(refreshed or rr, from_attributes=True).model_dump_json()

    @mcp.tool(
        name="create_group_request",
        title="Submit group request",
        description=(
            "Submit a group request — a request to CREATE a new group, role, "
            "or app group. 'group_type' is one of 'okta_group', 'role_group', "
            "or 'app_group'. For 'app_group', 'app_id' is REQUIRED and must "
            "name an existing app; the request routes to that app's owners. "
            "For other types, 'app_id' is ignored and the request routes to "
            "Access admins. On approval, the requester is added as an owner "
            "of the created group. 'tags_to_apply' is an optional list of tag "
            "ids to attach to the newly-created group. Requires the "
            "'create_requests' scope."
        ),
        annotations=_WRITE_PROPOSAL_ANNOTATIONS,
    )
    @requires_scope(MCP_SCOPE_CREATE_REQUESTS)
    def create_group_request(
        group_name: str,
        group_type: str,
        description: str = "",
        app_id: str = "",
        tags_to_apply: Optional[list[str]] = None,
        ownership_ending_at: str = "",
        reason: str = "",
    ) -> str:
        current_user_id = get_mcp_user_id()

        body_dict: dict[str, Any] = {
            "requested_group_name": group_name,
            "requested_group_description": description,
            "requested_group_type": group_type,
            "requested_group_tags": tags_to_apply or [],
            "request_reason": reason,
        }
        if app_id:
            body_dict["requested_app_id"] = app_id
        if ownership_ending_at:
            body_dict["requested_ownership_ending_at"] = ownership_ending_at

        # CreateGroupRequestBody is a discriminated union — validate
        # through a TypeAdapter so the right variant is picked and
        # field-level errors come back the same way they do for the
        # other write tools.
        try:
            body = TypeAdapter(CreateGroupRequestBody).validate_python(body_dict)
        except ValidationError as exc:
            return _validation_error(exc)

        from api.operations import CreateGroupRequest, RejectGroupRequest

        requested_app_id: Optional[str] = body.requested_app_id if isinstance(body, _AppGroupRequestBody) else None

        with mcp_db_session() as db:
            # Same gate as POST /api/group-requests: an active OktaUser
            # row is required. No additional role/admin check — anyone
            # authenticated can ask Access to create a group; the
            # approval step (not exposed here) is where the gating
            # happens.
            requester = (
                db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first()
            )
            if requester is None:
                return _error("Current user is not allowed to perform this action")

            # App-group requests must point at a real, non-deleted app.
            if body.requested_group_type == "app_group":
                if requested_app_id is None:
                    return _error("app_id is required for app group requests")
                app = db.query(App).filter(App.deleted_at.is_(None)).filter(App.id == requested_app_id).first()
                if app is None:
                    return _error("App not found")

            if body.requested_group_tags:
                tags = (
                    db.query(Tag).filter(Tag.deleted_at.is_(None)).filter(Tag.id.in_(body.requested_group_tags)).all()
                )
                if len(tags) != len(body.requested_group_tags):
                    return _error("One or more tags not found")

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
                return _error("Group request could not be created")
            refreshed = (
                db.query(GroupRequest).options(*_group_request_load_options()).filter(GroupRequest.id == gr.id).first()
            )
            _ctx = get_request_context()
            if _ctx is not None and _ctx.source == "mcp":
                logger.info(
                    f"MCP create_group_request id={gr.id} requester={current_user_id} "
                    f"group_name={body.requested_group_name!r} type={body.requested_group_type}"
                )
            return GroupRequestDetail.model_validate(refreshed or gr, from_attributes=True).model_dump_json()
