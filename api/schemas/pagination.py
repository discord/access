"""Generic pagination request and response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.audit_rows import AuditGroupRoleRow, AuditUserGroupRow
from api.schemas.core_schemas import (
    AppSummary,
    GroupSummary,
    OktaUserSummary,
    RoleGroupListItem,
    TagListItem,
)
from api.schemas.requests_schemas import (
    AccessRequestSummary,
    GroupRequestDetail,
    RoleRequestSummary,
)

T = TypeVar("T")


class SearchPaginationQuery(BaseModel):
    """Base for any list endpoint. `per_page=-1` is the legacy "all rows"
    sentinel — the pagination helper clamps it to MAX_PER_PAGE.

    `default=` is used explicitly on `page`/`per_page` so mypy without the
    pydantic plugin treats them as optional kwargs. Otherwise mypy sees
    `page: int = Field(0, ge=0)` as `page: int = FieldInfo(...)` and flags
    every test-side invocation of `SearchPaginationQuery()` as missing
    required args."""

    page: int = Field(default=0, ge=0)
    per_page: int = Field(default=50, ge=-1)
    q: Optional[str] = None


class SearchUserPaginationQuery(SearchPaginationQuery):
    pass


class SearchAppPaginationQuery(SearchPaginationQuery):
    pass


class SearchTagPaginationQuery(SearchPaginationQuery):
    pass


class SearchGroupPaginationQuery(SearchPaginationQuery):
    managed: Optional[bool] = None


class SearchRolePaginationQuery(SearchPaginationQuery):
    owner_id: Optional[str] = None


class SearchAccessRequestPaginationQuery(SearchPaginationQuery):
    status: Optional[str] = None
    requester_user_id: Optional[str] = None
    assignee_user_id: Optional[str] = None
    requested_group_id: Optional[str] = None
    resolver_user_id: Optional[str] = None


class SearchRoleRequestPaginationQuery(SearchAccessRequestPaginationQuery):
    requester_role_id: Optional[str] = None


class SearchGroupRequestPaginationQuery(SearchAccessRequestPaginationQuery):
    requested_group_type: Optional[str] = None
    requested_app_id: Optional[str] = None


class AuditOrderBy(str, Enum):
    moniker = "moniker"
    created_at = "created_at"
    ended_at = "ended_at"


class SearchAuditPaginationQuery(SearchPaginationQuery):
    """Common audit-endpoint filter set. Defaults match Flask: order
    newest-first by `created_at`."""

    owner: Optional[bool] = None
    active: Optional[bool] = None
    needs_review: Optional[bool] = None
    order_by: AuditOrderBy = AuditOrderBy.created_at
    order_desc: bool = True
    user_id: Optional[str] = None
    group_id: Optional[str] = None
    owner_id: Optional[str] = None
    app_owner: Optional[bool] = None
    managed: Optional[bool] = None
    start_date: Optional[int] = None
    end_date: Optional[int] = None
    direct: Optional[bool] = None
    deleted: Optional[bool] = None


class SearchUserGroupAuditPaginationQuery(SearchAuditPaginationQuery):
    pass


class SearchGroupRoleAuditPaginationQuery(SearchAuditPaginationQuery):
    role_id: Optional[str] = None
    role_owner_id: Optional[str] = None


# --- Response envelope ------------------------------------------------------
# Single generic envelope; named subclasses below give the OpenAPI codegen
# clean component names (`UserPagination`, ...) instead of synthetic
# `PaginationResponse_OktaUserSummary_` identifiers in the generated TS
# client. The `paginate()` helper in `api/pagination.py` emits all seven
# fields below.


class PaginationResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    total: int
    pages: int
    has_next: bool = False
    has_prev: bool = False
    next: Optional[str] = None
    prev: Optional[str] = None
    results: list[T]


class UserPagination(PaginationResponse[OktaUserSummary]):
    pass


class GroupPagination(PaginationResponse[GroupSummary]):
    pass


class RolePagination(PaginationResponse[RoleGroupListItem]):
    pass


class AppPagination(PaginationResponse[AppSummary]):
    pass


class TagPagination(PaginationResponse[TagListItem]):
    pass


class AccessRequestPagination(PaginationResponse[AccessRequestSummary]):
    pass


class RoleRequestPagination(PaginationResponse[RoleRequestSummary]):
    pass


class GroupRequestPagination(PaginationResponse[GroupRequestDetail]):
    pass


class UserGroupAuditPagination(PaginationResponse[AuditUserGroupRow]):
    pass


class GroupRoleAuditPagination(PaginationResponse[AuditGroupRoleRow]):
    pass
