"""Search filter query schemas for list endpoints.

Pagination itself (page / size / total / items) is provided by
`fastapi-pagination`'s `Page[T]` + `Params` in `api/pagination.py`.
The classes here carry only the per-endpoint *filter* keys (`q`, `status`,
`requester_user_id`, ...) — kept as separate `Query()` deps from `Params`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class SearchQuery(BaseModel):
    """Base free-text filter — every list endpoint accepts `?q=...`."""

    q: str | None = None


class SearchUserQuery(SearchQuery):
    pass


class SearchAppQuery(SearchQuery):
    pass


class SearchTagQuery(SearchQuery):
    pass


class SearchGroupQuery(SearchQuery):
    managed: bool | None = None


class SearchRoleQuery(SearchQuery):
    owner_id: str | None = None


class SearchAccessRequestQuery(SearchQuery):
    status: str | None = None
    requester_user_id: str | None = None
    assignee_user_id: str | None = None
    requested_group_id: str | None = None
    resolver_user_id: str | None = None


class SearchRoleRequestQuery(SearchAccessRequestQuery):
    requester_role_id: str | None = None


class SearchGroupRequestQuery(SearchAccessRequestQuery):
    requested_group_type: str | None = None
    requested_app_id: str | None = None


class AuditOrderBy(StrEnum):
    moniker = "moniker"
    created_at = "created_at"
    ended_at = "ended_at"


class SearchAuditQuery(SearchQuery):
    """Common audit-endpoint filter set. Defaults match Flask: order
    newest-first by `created_at`."""

    owner: bool | None = None
    active: bool | None = None
    needs_review: bool | None = None
    order_by: AuditOrderBy = AuditOrderBy.created_at
    order_desc: bool = True
    user_id: str | None = None
    group_id: str | None = None
    owner_id: str | None = None
    app_owner: bool | None = None
    managed: bool | None = None
    start_date: int | None = None
    end_date: int | None = None
    direct: bool | None = None
    deleted: bool | None = None


class SearchUserGroupAuditQuery(SearchAuditQuery):
    pass


class SearchGroupRoleAuditQuery(SearchAuditQuery):
    role_id: str | None = None
    role_owner_id: str | None = None
