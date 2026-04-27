"""Pydantic v2 schemas for the Access API.

These models replace the Marshmallow schemas under `api/views/schemas/`.
Each endpoint declares one Pydantic model whose field set is exactly the
JSON it returns — there is no `only=`/`exclude=` projection plumbing.

Polymorphic groups are modelled as discriminated unions on the `type` field.
"""

from api.schemas.audit_logs import AuditLogSchema, EventType  # noqa: F401
from api.schemas.core_schemas import (  # noqa: F401
    AppDetail,
    AppGroupDetail,
    AppGroupIn,
    AppGroupRef,
    AppGroupSummary,
    AppIdRef,
    AppSummary,
    AppTagMapDetail,
    GroupDetail,
    GroupIn,
    GroupRef,
    GroupSummary,
    OktaGroupDetail,
    OktaGroupIn,
    OktaGroupRef,
    OktaGroupSummary,
    OktaGroupTagMapDetail,
    OktaUserDetail,
    OktaUserGroupMemberDetail,
    OktaUserSummary,
    RoleGroupDetail,
    RoleGroupIn,
    RoleGroupMapDetail,
    RoleGroupRef,
    RoleGroupSummary,
    TagDetail,
)
from api.schemas.delete_message import DeleteMessage  # noqa: F401
from api.schemas.pagination import (  # noqa: F401
    AccessRequestPagination,
    AppPagination,
    GroupPagination,
    GroupRequestPagination,
    GroupRoleAuditPagination,
    PaginationResponse,
    RolePagination,
    RoleRequestPagination,
    SearchAccessRequestPaginationRequest,
    SearchAuditPaginationRequest,
    SearchGroupPaginationRequest,
    SearchGroupRequestPaginationRequest,
    SearchGroupRoleAuditPaginationRequest,
    SearchPaginationRequest,
    SearchRolePaginationRequest,
    SearchRoleRequestPaginationRequest,
    SearchUserGroupAuditPaginationRequest,
    TagPagination,
    UserGroupAuditPagination,
    UserPagination,
)
from api.schemas.requests_schemas import (  # noqa: F401
    AccessRequestDetail,
    CreateAccessRequest,
    CreateGroupRequest,
    CreateRoleRequest,
    GroupMember,
    GroupRequestDetail,
    ResolveAccessRequest,
    ResolveGroupRequest,
    ResolveRoleRequest,
    RoleMember,
    RoleRequestDetail,
)
