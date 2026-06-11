"""Pydantic v2 schemas for the Access API.

Each endpoint declares one Pydantic model whose field set is exactly the
JSON it returns — there is no per-call field projection plumbing. Where
the same entity is exposed in multiple shapes (full read vs. compact list
vs. embedded reference), define separate models.

Polymorphic groups are modelled as discriminated unions on the `type`
field.
"""

from api.schemas.audit_logs import AuditLogSchema, EventType  # noqa: F401
from api.schemas.audit_rows import AuditGroupRoleRow, AuditUserGroupRow  # noqa: F401
from api.schemas.core_schemas import (  # noqa: F401
    AppDetail,
    AppGroupDetail,
    AppGroupForAppDetail,
    AppGroupRef,
    AppGroupSummary,
    AppIdRef,
    AppSummary,
    AppTagMapDetail,
    GroupDetail,
    GroupMembersSummary,
    GroupRef,
    GroupSummary,
    OktaGroupDetail,
    OktaGroupRef,
    OktaGroupSummary,
    OktaGroupTagMapDetail,
    OktaUserDetail,
    OktaUserGroupMemberDetail,
    OktaUserSummary,
    ProblemDetail,
    ProblemDetailError,
    RoleGroupDetail,
    RoleGroupListItem,
    RoleGroupMapDetail,
    RoleGroupRef,
    RoleGroupSummary,
    RoleMembersSummary,
    TagDetail,
    TagListItem,
    TagSummary,
)
from api.schemas.delete_message import DeleteMessage  # noqa: F401
from api.schemas.plugin_schemas import (  # noqa: F401
    AppGroupLifecyclePluginAppConfig,
    AppGroupLifecyclePluginAppStatus,
    AppGroupLifecyclePluginGroupConfig,
    AppGroupLifecyclePluginGroupStatus,
    AppGroupLifecyclePlugins,
    PluginConfigProp,
    PluginInfo,
    PluginStatusProp,
)
from api.schemas.pagination import (  # noqa: F401
    AuditOrderBy,
    SearchAccessRequestQuery,
    SearchAppQuery,
    SearchAuditQuery,
    SearchGroupQuery,
    SearchGroupRequestQuery,
    SearchGroupRoleAuditQuery,
    SearchQuery,
    SearchRoleQuery,
    SearchRoleRequestQuery,
    SearchTagQuery,
    SearchUserGroupAuditQuery,
    SearchUserQuery,
)
from api.schemas.requests_schemas import (  # noqa: F401
    AccessRequestDetail,
    AccessRequestSummary,
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
    RoleRequestSummary,
    UpdateAppBody,
    UpdateGroupBody,
    UpdateTagBody,
)
