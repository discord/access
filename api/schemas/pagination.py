"""Generic pagination request and response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class SearchPaginationQuery(BaseModel):
    """Base for any list endpoint. `per_page=-1` is the legacy "all rows"
    sentinel — the pagination helper clamps it to MAX_PER_PAGE."""

    page: int = Field(0, ge=0)
    per_page: int = Field(50, ge=-1)
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


class PaginationResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    total: int
    pages: int
    next: Optional[str] = None
    prev: Optional[str] = None
    results: list[T]


# Concrete envelopes (Pydantic v2 generic instantiation)
class _PageBase(BaseModel):
    total: int
    pages: int
    next: Optional[str] = None
    prev: Optional[str] = None


class UserPagination(_PageBase):
    results: list[Any]


class GroupPagination(_PageBase):
    results: list[Any]


class RolePagination(_PageBase):
    results: list[Any]


class AppPagination(_PageBase):
    results: list[Any]


class TagPagination(_PageBase):
    results: list[Any]


class AccessRequestPagination(_PageBase):
    results: list[Any]


class RoleRequestPagination(_PageBase):
    results: list[Any]


class GroupRequestPagination(_PageBase):
    results: list[Any]


class UserGroupAuditPagination(_PageBase):
    results: list[Any]


class GroupRoleAuditPagination(_PageBase):
    results: list[Any]
