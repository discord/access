"""
FastAPI/Pydantic pagination schemas.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from api.models import AccessRequestStatus
from api_v2.schemas.base import BaseSchema
from api_v2.schemas.groups import GroupRead, RoleGroupRead
from api_v2.schemas.requests import AccessRequestRead, RoleRequestRead
from api_v2.schemas.tags import TagRead
from api_v2.schemas.users import UserGroupMember, UserSummary


class SearchPagination(BaseSchema):
    """Base search pagination parameters."""

    page: Optional[int] = Field(None, ge=1, description="Page number")
    per_page: Optional[int] = Field(None, ge=1, le=100, description="Items per page")
    q: Optional[str] = Field(None, description="Search query")


class SearchGroupPagination(SearchPagination):
    """Group search pagination parameters."""

    managed: Optional[bool] = Field(None, description="Filter by managed groups")


class SearchRolePagination(SearchPagination):
    """Role search pagination parameters."""

    owner_id: Optional[str] = Field(None, description="Filter by owner ID")


class AuditOrderBy(str, Enum):
    """Audit log ordering options."""

    name = "name"
    created_at = "created_at"
    ended_at = "ended_at"


class SearchAuditPagination(SearchPagination):
    """Audit search pagination parameters."""

    owner: Optional[bool] = Field(None, description="Filter by ownership")
    active: Optional[bool] = Field(None, description="Filter by active status")
    needs_review: Optional[bool] = Field(None, description="Filter by review needed")
    managed: Optional[bool] = Field(None, description="Filter by managed status")
    order_by: AuditOrderBy = Field(default=AuditOrderBy.created_at, description="Field to order by")
    order_desc: bool = Field(default=True, description="Order descending")


class SearchUserGroupAuditPagination(SearchAuditPagination):
    """User-group audit search pagination parameters."""

    user_id: Optional[str] = Field(None, description="Filter by user ID")
    group_id: Optional[str] = Field(None, description="Filter by group ID")
    owner_id: Optional[str] = Field(None, description="Filter by owner ID")
    direct: Optional[bool] = Field(None, description="Show only direct reports")
    deleted: Optional[bool] = Field(None, description="Include deleted users")
    start_date: Optional[int] = Field(None, description="Start date timestamp")
    end_date: Optional[int] = Field(None, description="End date timestamp")


class SearchGroupRoleAuditPagination(SearchAuditPagination):
    """Group-role audit search pagination parameters."""

    group_id: Optional[str] = Field(None, description="Filter by group ID")
    role_id: Optional[str] = Field(None, description="Filter by role ID")
    owner_id: Optional[str] = Field(None, description="Filter by owner ID")
    role_owner_id: Optional[str] = Field(None, description="Filter by role owner ID")
    app_owner: Optional[bool] = Field(None, description="Filter by app owner")
    start_date: Optional[int] = Field(None, description="Start date timestamp")
    end_date: Optional[int] = Field(None, description="End date timestamp")


class SearchAccessRequestPagination(SearchPagination):
    """Access request search pagination parameters."""

    status: Optional[AccessRequestStatus] = Field(None, description="Filter by status")
    requester_user_id: Optional[str] = Field(None, description="Filter by requester")
    requested_group_id: Optional[str] = Field(None, description="Filter by requested group")
    assignee_user_id: Optional[str] = Field(None, description="Filter by assignee")
    resolver_user_id: Optional[str] = Field(None, description="Filter by resolver")


class SearchRoleRequestPagination(SearchPagination):
    """Role request search pagination parameters."""

    status: Optional[AccessRequestStatus] = Field(None, description="Filter by status")
    requester_user_id: Optional[str] = Field(None, description="Filter by requester")
    requester_role_id: Optional[str] = Field(None, description="Filter by requester role")
    requested_group_id: Optional[str] = Field(None, description="Filter by requested group")
    assignee_user_id: Optional[str] = Field(None, description="Filter by assignee")
    resolver_user_id: Optional[str] = Field(None, description="Filter by resolver")


class PaginationResponse(BaseModel):
    """Base pagination response."""

    total: int = Field(..., description="Total number of items")
    pages: int = Field(..., description="Total number of pages")
    next: Optional[str] = Field(None, description="URL for next page")
    prev: Optional[str] = Field(None, description="URL for previous page")


class AccessRequestPagination(PaginationResponse):
    """Paginated access request response."""

    results: List[AccessRequestRead] = Field(..., description="Access requests")


class RoleRequestPagination(PaginationResponse):
    """Paginated role request response."""

    results: List[RoleRequestRead] = Field(..., description="Role requests")


class AppPagination(PaginationResponse):
    """Paginated app response."""

    results: List["AppRead"] = Field(..., description="Apps")


class UserGroupAuditPagination(PaginationResponse):
    """Paginated user-group audit response."""

    results: List[UserGroupMember] = Field(..., description="User group memberships")


class GroupPagination(PaginationResponse):
    """Paginated group response."""

    results: List[GroupRead] = Field(..., description="Groups")


class GroupRoleAuditPagination(PaginationResponse):
    """Paginated group-role audit response."""

    results: List["RoleGroupMap"] = Field(..., description="Role group mappings")


class RolePagination(PaginationResponse):
    """Paginated role response."""

    results: List[RoleGroupRead] = Field(..., description="Roles")


class TagPagination(PaginationResponse):
    """Paginated tag response."""

    results: List[TagRead] = Field(..., description="Tags")


class UserPagination(PaginationResponse):
    """Paginated user response."""

    results: List[UserSummary] = Field(..., description="Users")
