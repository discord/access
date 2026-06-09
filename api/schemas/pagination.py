"""Search filter query schemas for list endpoints.

Pagination itself (page / size / total / items) is provided by
`fastapi-pagination`'s `Page[T]` + `Params` in `api/pagination.py`.
The classes here carry only the per-endpoint *filter* keys (`q`, `status`,
`requester_user_id`, ...) — kept as separate `Query()` deps from `Params`.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SearchQuery(BaseModel):
    """Base free-text filter — every list endpoint accepts `?q=...`."""

    q: Optional[str] = None


class SearchUserQuery(SearchQuery):
    pass


class SearchAppQuery(SearchQuery):
    pass


class SearchTagQuery(SearchQuery):
    pass


class SearchGroupQuery(SearchQuery):
    managed: Optional[bool] = None


class SearchRoleQuery(SearchQuery):
    owner_id: Optional[str] = None


class SearchAccessRequestQuery(SearchQuery):
    status: Optional[str] = None
    requester_user_id: Optional[str] = None
    assignee_user_id: Optional[str] = None
    requested_group_id: Optional[str] = None
    resolver_user_id: Optional[str] = None


class SearchRoleRequestQuery(SearchAccessRequestQuery):
    requester_role_id: Optional[str] = None


class SearchGroupRequestQuery(SearchAccessRequestQuery):
    requested_group_type: Optional[str] = None
    requested_app_id: Optional[str] = None


class AuditOrderBy(str, Enum):
    moniker = "moniker"
    created_at = "created_at"
    ended_at = "ended_at"


class SearchAuditQuery(SearchQuery):
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


class SearchUserGroupAuditQuery(SearchAuditQuery):
    pass


class SearchGroupRoleAuditQuery(SearchAuditQuery):
    role_id: Optional[str] = None
    role_owner_id: Optional[str] = None
