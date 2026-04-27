"""Generic pagination request and response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class SearchPaginationRequest(BaseModel):
    page: int = Field(0, ge=0)
    per_page: int = Field(50, ge=-1)
    q: Optional[str] = None


class SearchGroupPaginationRequest(SearchPaginationRequest):
    managed: Optional[bool] = None


class SearchAccessRequestPaginationRequest(SearchPaginationRequest):
    status: Optional[str] = None
    requester_user_id: Optional[str] = None
    assignee_user_id: Optional[str] = None
    requested_group_id: Optional[str] = None
    resolver_user_id: Optional[str] = None


class SearchRoleRequestPaginationRequest(SearchAccessRequestPaginationRequest):
    requester_role_id: Optional[str] = None


class SearchGroupRequestPaginationRequest(SearchAccessRequestPaginationRequest):
    pass


class AuditOrderBy(str, Enum):
    moniker = "moniker"
    created_at = "created_at"
    ended_at = "ended_at"


class SearchAuditPaginationRequest(SearchPaginationRequest):
    owner: Optional[bool] = None
    active: Optional[bool] = None
    needs_review: Optional[bool] = None
    order_by: AuditOrderBy = AuditOrderBy.moniker
    order_desc: bool = False
    user_id: Optional[str] = None
    group_id: Optional[str] = None
    direct: Optional[bool] = None


class SearchUserGroupAuditPaginationRequest(SearchAuditPaginationRequest):
    pass


class SearchGroupRoleAuditPaginationRequest(SearchAuditPaginationRequest):
    role_id: Optional[str] = None


class SearchRolePaginationRequest(SearchPaginationRequest):
    pass


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
