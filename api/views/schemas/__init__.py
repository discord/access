from api.views.schemas.access_requests import (
    CreateAccessRequestSchema,
    ResolveAccessRequestSchema,
)
from api.views.schemas.audit_logs import AuditLogSchema, EventType
from api.views.schemas.core_schemas import (
    AccessRequestSchema,
    AppGroupSchema,
    AppSchema,
    AppTagMapSchema,
    OktaGroupSchema,
    OktaGroupTagMapSchema,
    OktaUserGroupMemberSchema,
    OktaUserSchema,
    PolymorphicGroupSchema,
    RoleGroupMapSchema,
    RoleGroupSchema,
    RoleRequestSchema,
    TagSchema,
)
from api.views.schemas.delete_message import DeleteMessageSchema
from api.views.schemas.group_memberships import GroupMemberSchema
from api.views.schemas.metrics import MetricsSchema
from api.views.schemas.pagination import (
    AccessRequestPaginationSchema,
    AppPaginationSchema,
    AuditOrderBy,
    GroupPaginationSchema,
    GroupRoleAuditPaginationSchema,
    RolePaginationSchema,
    RoleRequestPaginationSchema,
    SearchAccessRequestPaginationRequestSchema,
    SearchAuditPaginationRequestSchema,
    SearchGroupPaginationRequestSchema,
    SearchGroupRoleAuditPaginationRequestSchema,
    SearchPaginationRequestSchema,
    SearchRolePaginationRequestSchema,
    SearchRoleRequestPaginationRequestSchema,
    SearchUserGroupAuditPaginationRequestSchema,
    TagPaginationSchema,
    UserGroupAuditPaginationSchema,
    UserPaginationSchema,
)
from api.views.schemas.role_memberships import RoleMemberSchema
from api.views.schemas.role_requests import CreateRoleRequestSchema, ResolveRoleRequestSchema

__all__ = [
    "AccessRequestPaginationSchema",
    "AccessRequestSchema",
    "AppGroupSchema",
    "AppPaginationSchema",
    "AppSchema",
    "AppTagMapSchema",
    "AuditLogSchema",
    "AuditOrderBy",
    "CreateAccessRequestSchema",
    "CreateRoleRequestSchema",
    "DeleteMessageSchema",
    "EventType",
    "GroupMemberSchema",
    "GroupPaginationSchema",
    "GroupRoleAuditPaginationSchema",
    "MetricsSchema",
    "OktaGroupSchema",
    "OktaGroupTagMapSchema",
    "OktaUserGroupMemberSchema",
    "OktaUserSchema",
    "PolymorphicGroupSchema",
    "ResolveAccessRequestSchema",
    "ResolveRoleRequestSchema",
    "RoleGroupMapSchema",
    "RoleGroupSchema",
    "RoleMemberSchema",
    "RolePaginationSchema",
    "RoleRequestPaginationSchema",
    "RoleRequestSchema",
    "SearchAccessRequestPaginationRequestSchema",
    "SearchAuditPaginationRequestSchema",
    "SearchGroupPaginationRequestSchema",
    "SearchGroupRoleAuditPaginationRequestSchema",
    "SearchPaginationRequestSchema",
    "SearchRolePaginationRequestSchema",
    "SearchRoleRequestPaginationRequestSchema",
    "SearchUserGroupAuditPaginationRequestSchema",
    "TagPaginationSchema",
    "TagSchema",
    "UserGroupAuditPaginationSchema",
    "UserPaginationSchema",
]

# Monkeypatch marshmallow to use rfc822 format for datetime, so timezone offset is included
from marshmallow.fields import DateTime

DateTime.DEFAULT_FORMAT = "rfc822"
