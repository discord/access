"""
API v2 schemas initialization and forward reference resolution.
"""

from __future__ import annotations

from api_v2.schemas.audit_logs import (
    AuditAccessRequestSummary,
    AuditAppSummary,
    AuditEventType,
    AuditGroupSummary,
    AuditLogCreate,
    AuditLogRead,
    AuditRoleGroupMapSummary,
    AuditRoleGroupSummary,
    AuditRoleRequestSummary,
    AuditTagSummary,
    AuditUserGroupMembershipSummary,
    AuditUserSummary,
)

# Import base schemas first
from api_v2.schemas.base import BaseCreateSchema, BaseReadSchema, BaseSchema, BaseUpdateSchema

# Make specific schemas available at package level
from api_v2.schemas.core_schemas import (
    # Group schemas
    AppGroupCreate,
    # App schemas
    AppGroupRead,
    AppGroupUpdate,
    AppRead,
    AppSummary,
    AppTagMap,
    AppTagMappingRead,
    GroupBase,
    GroupCreate,
    GroupList,
    GroupMemberAction,
    GroupMemberList,
    GroupRead,
    GroupReadBase,
    GroupSearch,
    GroupSummary,
    GroupTagMappingRead,
    GroupUpdate,
    GroupUpdateBase,
    NonRoleGroupReadBase,
    OktaGroupCreate,
    OktaGroupRead,
    OktaGroupTagMap,
    OktaGroupUpdate,
    RoleGroupCreate,
    RoleGroupMap,
    RoleGroupRead,
    RoleGroupUpdate,
    # Tag schemas
    TagBase,
    TagCreate,
    TagList,
    TagMappingBase,
    TagMappingList,
    TagRead,
    TagSearch,
    TagSummary,
    TagUpdate,
    # User schemas
    UserBase,
    UserDetail,
    UserGroupMember,
    UserList,
    UserSearch,
    UserSummary,
)
from api_v2.schemas.delete_message import (
    DeleteMessage,
)
from api_v2.schemas.group_memberships import (
    GroupMemberRead,
    GroupMemberUpdate,
)
from api_v2.schemas.metrics import (
    Metrics,
    MetricsData,
    MetricType,
)
from api_v2.schemas.pagination import (
    AccessRequestPagination,
    AppPagination,
    AuditOrderBy,
    GroupPagination,
    GroupRoleAuditPagination,
    PaginationResponse,
    RolePagination,
    RoleRequestPagination,
    SearchAccessRequestPagination,
    SearchAuditPagination,
    SearchGroupPagination,
    SearchGroupRoleAuditPagination,
    SearchPagination,
    SearchRolePagination,
    SearchRoleRequestPagination,
    SearchUserGroupAuditPagination,
    TagPagination,
    UserGroupAuditPagination,
    UserPagination,
)
from api_v2.schemas.requests import (
    AccessRequestCreate,
    AccessRequestRead,
    AccessRequestResolve,
    AccessRequestSearch,
    AccessRequestStatus,
    RoleRequestCreate,
    RoleRequestRead,
    RoleRequestResolve,
    RoleRequestSearch,
)
from api_v2.schemas.role_memberships import (
    RoleMemberRead,
    RoleMemberUpdate,
)

__all__ = [
    # Base schemas
    "BaseSchema",
    "BaseReadSchema",
    "BaseCreateSchema",
    "BaseUpdateSchema",
    # App schemas
    "AppGroupRead",
    "AppRead",
    "AppSummary",
    "AppTagMap",
    "AppTagMappingRead",
    # Audit log schemas
    "AuditAccessRequestSummary",
    "AuditAppSummary",
    "AuditEventType",
    "AuditGroupSummary",
    "AuditLogCreate",
    "AuditLogRead",
    "AuditRoleGroupSummary",
    "AuditRoleGroupMapSummary",
    "AuditRoleRequestSummary",
    "AuditTagSummary",
    "AuditUserGroupMembershipSummary",
    "AuditUserSummary",
    # Delete message schemas
    "DeleteMessage",
    # Group schemas
    "AppGroupCreate",
    "AppGroupUpdate",
    "GroupBase",
    "GroupCreate",
    "GroupList",
    "GroupMemberAction",
    "GroupMemberList",
    "GroupRead",
    "GroupReadBase",
    "GroupSearch",
    "GroupSummary",
    "GroupTagMappingRead",
    "GroupUpdate",
    "GroupUpdateBase",
    "NonRoleGroupReadBase",
    "OktaGroupCreate",
    "OktaGroupRead",
    "OktaGroupTagMap",
    "OktaGroupUpdate",
    "RoleGroupCreate",
    "RoleGroupMap",
    "RoleGroupRead",
    "RoleGroupUpdate",
    # Group membership schemas
    "GroupMemberRead",
    "GroupMemberUpdate",
    # Metrics schemas
    "Metrics",
    "MetricsData",
    "MetricType",
    # Pagination schemas
    "AccessRequestPagination",
    "AppPagination",
    "AuditOrderBy",
    "GroupPagination",
    "GroupRoleAuditPagination",
    "PaginationResponse",
    "RolePagination",
    "RoleRequestPagination",
    "SearchAccessRequestPagination",
    "SearchAuditPagination",
    "SearchGroupPagination",
    "SearchGroupRoleAuditPagination",
    "SearchPagination",
    "SearchRolePagination",
    "SearchRoleRequestPagination",
    "SearchUserGroupAuditPagination",
    "TagPagination",
    "UserGroupAuditPagination",
    "UserPagination",
    # Request schemas
    "AccessRequestStatus",
    "AccessRequestRead",
    "AccessRequestCreate",
    "AccessRequestResolve",
    "RoleRequestRead",
    "RoleRequestCreate",
    "RoleRequestResolve",
    "AccessRequestSearch",
    "RoleRequestSearch",
    # Role membership schemas
    "RoleMemberRead",
    "RoleMemberUpdate",
    # Tag schemas
    "TagBase",
    "TagCreate",
    "TagList",
    "TagMappingBase",
    "TagMappingList",
    "TagRead",
    "TagSearch",
    "TagSummary",
    "TagUpdate",
    # User schemas
    "UserBase",
    "UserDetail",
    "UserGroupMember",
    "UserList",
    "UserSearch",
    "UserSummary",
]
