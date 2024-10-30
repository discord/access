from api.views.resources.access_request import AccessRequestList, AccessRequestResource
from api.views.resources.app import AppList, AppResource
from api.views.resources.audit import GroupRoleAuditResource, UserGroupAuditResource
from api.views.resources.bug import SentryProxyResource
from api.views.resources.group import GroupAuditResource, GroupList, GroupMemberResource, GroupResource
from api.views.resources.role import RoleAuditResource, RoleList, RoleMemberResource, RoleResource
from api.views.resources.role_request import RoleRequestList, RoleRequestResource
from api.views.resources.tag import TagList, TagResource
from api.views.resources.user import UserAuditResource, UserList, UserResource
from api.views.resources.webhook import OktaWebhookResource

__all__ = [
    "AccessRequestList",
    "AccessRequestResource",
    "AppList",
    "AppResource",
    "GroupAuditResource",
    "GroupList",
    "GroupMemberResource",
    "GroupResource",
    "GroupRoleAuditResource",
    "OktaWebhookResource",
    "RoleAuditResource",
    "RoleList",
    "RoleMemberResource",
    "RoleRequestList",
    "RoleRequestResource",
    "RoleResource",
    "SentryProxyResource",
    "TagList",
    "TagResource",
    "UserAuditResource",
    "UserGroupAuditResource",
    "UserList",
    "UserResource",
]
