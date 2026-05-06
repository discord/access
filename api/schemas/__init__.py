"""Pydantic v2 schemas for the Access API.

Each endpoint declares one Pydantic model whose field set is exactly the
JSON it returns — there is no per-call field projection plumbing. Where
the same entity is exposed in multiple shapes (full read vs. compact list
vs. embedded reference), define separate models.

Polymorphic groups are modelled as discriminated unions on the `type`
field.
"""

from api.schemas.audit_logs import AuditLogSchema, EventType  # noqa: F401
from api.schemas.core_schemas import (  # noqa: F401
    AppDetail,
    AppGroupDetail,
    AppGroupRef,
    AppGroupSummary,
    AppIdRef,
    AppSummary,
    AppTagMapDetail,
    GroupDetail,
    GroupRef,
    GroupSummary,
    OktaGroupDetail,
    OktaGroupRef,
    OktaGroupSummary,
    OktaGroupTagMapDetail,
    OktaUserDetail,
    OktaUserGroupMemberDetail,
    OktaUserSummary,
    RoleGroupDetail,
    RoleGroupMapDetail,
    RoleGroupRef,
    RoleGroupSummary,
    TagDetail,
)
from api.schemas.delete_message import DeleteMessage  # noqa: F401
from api.schemas.pagination import (  # noqa: F401
    AccessRequestPagination,
    AppPagination,
    AuditOrderBy,
    GroupPagination,
    GroupRequestPagination,
    GroupRoleAuditPagination,
    PaginationResponse,
    RolePagination,
    RoleRequestPagination,
    SearchAccessRequestPaginationQuery,
    SearchAppPaginationQuery,
    SearchAuditPaginationQuery,
    SearchGroupPaginationQuery,
    SearchGroupRequestPaginationQuery,
    SearchGroupRoleAuditPaginationQuery,
    SearchPaginationQuery,
    SearchRolePaginationQuery,
    SearchRoleRequestPaginationQuery,
    SearchTagPaginationQuery,
    SearchUserGroupAuditPaginationQuery,
    SearchUserPaginationQuery,
    TagPagination,
    UserGroupAuditPagination,
    UserPagination,
)
from api.schemas.requests_schemas import (  # noqa: F401
    AccessRequestDetail,
    CreateAccessRequestBody,
    CreateAppBody,
    CreateGroupBody,
    CreateGroupRequestBody,
    CreateRoleRequestBody,
    CreateTagBody,
    GroupMember,
    GroupRequestDetail,
    ResolveAccessRequestBody,
    ResolveGroupRequestBody,
    ResolveRoleRequestBody,
    RoleMember,
    RoleRequestDetail,
    UpdateAppBody,
    UpdateGroupBody,
    UpdateTagBody,
)
