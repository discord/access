"""
FastAPI/Pydantic pagination schemas.
"""

from enum import Enum

from pydantic import BaseModel, Field

from api_v2.models import AccessRequestStatus
from api_v2.schemas.base import BaseSchema
from api_v2.schemas.core_schemas import GroupRead, RoleGroupRead, TagRead, UserGroupMember, UserSummary
from api_v2.schemas.requests import AccessRequestRead, RoleRequestRead


class SearchPagination(BaseSchema):
    """Base search pagination parameters."""

    page: int | None = Field(None, ge=1, description="Page number")
    per_page: int | None = Field(None, ge=1, le=100, description="Items per page")
    q: str | None = Field(None, description="Search query")


class SearchGroupPagination(SearchPagination):
    """Group search pagination parameters."""

    managed: bool | None = Field(None, description="Filter by managed groups")


class SearchRolePagination(SearchPagination):
    """Role search pagination parameters."""

    owner_id: str | None = Field(None, description="Filter by owner ID")


class AuditOrderBy(str, Enum):
    """Audit log ordering options."""

    name = "name"
    created_at = "created_at"
    ended_at = "ended_at"


class SearchAuditPagination(SearchPagination):
    """Audit search pagination parameters."""

    owner: bool | None = Field(None, description="Filter by ownership")
    active: bool | None = Field(None, description="Filter by active status")
    needs_review: bool | None = Field(None, description="Filter by review needed")
    managed: bool | None = Field(None, description="Filter by managed status")
    order_by: AuditOrderBy = Field(default=AuditOrderBy.created_at, description="Field to order by")
    order_desc: bool = Field(default=True, description="Order descending")


class SearchUserGroupAuditPagination(SearchAuditPagination):
    """User-group audit search pagination parameters."""

    user_id: str | None = Field(None, description="Filter by user ID")
    group_id: str | None = Field(None, description="Filter by group ID")
    owner_id: str | None = Field(None, description="Filter by owner ID")
    direct: bool | None = Field(None, description="Show only direct reports")
    deleted: bool | None = Field(None, description="Include deleted users")
    start_date: int | None = Field(None, description="Start date timestamp")
    end_date: int | None = Field(None, description="End date timestamp")


class SearchGroupRoleAuditPagination(SearchAuditPagination):
    """Group-role audit search pagination parameters."""

    group_id: str | None = Field(None, description="Filter by group ID")
    role_id: str | None = Field(None, description="Filter by role ID")
    owner_id: str | None = Field(None, description="Filter by owner ID")
    role_owner_id: str | None = Field(None, description="Filter by role owner ID")
    app_owner: bool | None = Field(None, description="Filter by app owner")
    start_date: int | None = Field(None, description="Start date timestamp")
    end_date: int | None = Field(None, description="End date timestamp")


class SearchAccessRequestPagination(SearchPagination):
    """Access request search pagination parameters."""

    status: AccessRequestStatus | None = Field(None, description="Filter by status")
    requester_user_id: str | None = Field(None, description="Filter by requester")
    requested_group_id: str | None = Field(None, description="Filter by requested group")
    assignee_user_id: str | None = Field(None, description="Filter by assignee")
    resolver_user_id: str | None = Field(None, description="Filter by resolver")


class SearchRoleRequestPagination(SearchPagination):
    """Role request search pagination parameters."""

    status: AccessRequestStatus | None = Field(None, description="Filter by status")
    requester_user_id: str | None = Field(None, description="Filter by requester")
    requester_role_id: str | None = Field(None, description="Filter by requester role")
    requested_group_id: str | None = Field(None, description="Filter by requested group")
    assignee_user_id: str | None = Field(None, description="Filter by assignee")
    resolver_user_id: str | None = Field(None, description="Filter by resolver")


class PaginationResponse(BaseModel):
    """Base pagination response."""

    total: int = Field(..., description="Total number of items")
    pages: int = Field(..., description="Total number of pages")
    next: str | None = Field(None, description="URL for next page")
    prev: str | None = Field(None, description="URL for previous page")


class AccessRequestPagination(PaginationResponse):
    """Paginated access request response."""

    results: list[AccessRequestRead] = Field(..., description="Access requests")


class RoleRequestPagination(PaginationResponse):
    """Paginated role request response."""

    results: list[RoleRequestRead] = Field(..., description="Role requests")


class AppPagination(PaginationResponse):
    """Paginated app response."""

    results: list["AppRead"] = Field(..., description="Apps")


class UserGroupAuditPagination(PaginationResponse):
    """Paginated user-group audit response."""

    results: list[UserGroupMember] = Field(..., description="User group memberships")


class GroupPagination(PaginationResponse):
    """Paginated group response."""

    results: list[GroupRead] = Field(..., description="Groups")


class GroupRoleAuditPagination(PaginationResponse):
    """Paginated group-role audit response."""

    results: list["RoleGroupMap"] = Field(..., description="Role group mappings")


class RolePagination(PaginationResponse):
    """Paginated role response."""

    results: list[RoleGroupRead] = Field(..., description="Roles")


class TagPagination(PaginationResponse):
    """Paginated tag response."""

    results: list[TagRead] = Field(..., description="Tags")


class UserPagination(PaginationResponse):
    """Paginated user response."""

    results: list[UserSummary] = Field(..., description="Users")
