from enum import Enum

from marshmallow import Schema, fields

from api.models import AccessRequestStatus
from api.views.schemas.core_schemas import (
    AccessRequestSchema,
    AppSchema,
    OktaUserGroupMemberSchema,
    OktaUserSchema,
    PolymorphicGroupSchema,
    RoleGroupMapSchema,
    RoleGroupSchema,
    RoleRequestSchema,
    TagSchema,
)


class SearchPaginationRequestSchema(Schema):
    page = fields.Int(load_only=True)
    per_page = fields.Int(load_only=True)
    q = fields.String(load_only=True)


class SearchGroupPaginationRequestSchema(SearchPaginationRequestSchema):
    managed = fields.Boolean(load_only=True)


class SearchRolePaginationRequestSchema(SearchPaginationRequestSchema):
    owner_id = fields.String(load_only=True)


class AuditOrderBy(Enum):
    moniker = 1  # Enum has a field called "name", so we can't use that
    created_at = 2
    ended_at = 3


class SearchAuditPaginationRequestSchema(SearchPaginationRequestSchema):
    owner = fields.Boolean(load_only=True)
    active = fields.Boolean(load_only=True)
    needs_review = fields.Boolean(load_only=True)
    managed = fields.Boolean(load_only=True)
    order_by = fields.Enum(AuditOrderBy, load_only=True, load_default=AuditOrderBy.created_at)
    order_desc = fields.Boolean(load_only=True, load_default=True)


class SearchUserGroupAuditPaginationRequestSchema(SearchAuditPaginationRequestSchema):
    user_id = fields.String(load_only=True)
    group_id = fields.String(load_only=True)
    owner_id = fields.String(load_only=True)
    direct = fields.Boolean(load_only=True)  # Set to true to only show direct reports
    deleted = fields.Boolean(load_only=True)  # Set to false to only show active users
    start_date = fields.Int(load_only=True)
    end_date = fields.Int(load_only=True)


class SearchGroupRoleAuditPaginationRequestSchema(SearchAuditPaginationRequestSchema):
    group_id = fields.String(load_only=True)
    role_id = fields.String(load_only=True)
    owner_id = fields.String(load_only=True)
    role_owner_id = fields.String(load_only=True)
    app_owner = fields.Boolean(load_only=True)
    start_date = fields.Int(load_only=True)
    end_date = fields.Int(load_only=True)


class SearchAccessRequestPaginationRequestSchema(SearchPaginationRequestSchema):
    status = fields.Enum(AccessRequestStatus, load_only=True)
    requester_user_id = fields.String(load_only=True)
    requested_group_id = fields.String(load_only=True)
    assignee_user_id = fields.String(load_only=True)
    resolver_user_id = fields.String(load_only=True)


class SearchRoleRequestPaginationRequestSchema(SearchPaginationRequestSchema):
    status = fields.Enum(AccessRequestStatus, load_only=True)
    requester_user_id = fields.String(load_only=True)
    requester_role_id = fields.String(load_only=True)
    requested_group_id = fields.String(load_only=True)
    assignee_user_id = fields.String(load_only=True)
    resolver_user_id = fields.String(load_only=True)


class PaginationResponseSchema(Schema):
    total = fields.Int(dump_only=True)
    pages = fields.Int(dump_only=True)
    next = fields.Url(dump_only=True)
    prev = fields.Url(dump_only=True)


class AccessRequestPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: AccessRequestSchema(many=True), dump_only=True)


class RoleRequestPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: RoleRequestSchema(many=True), dump_only=True)


class AppPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: AppSchema(many=True), dump_only=True)


class UserGroupAuditPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: OktaUserGroupMemberSchema(many=True), dump_only=True)


class GroupPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: PolymorphicGroupSchema(many=True), dump_only=True)


class GroupRoleAuditPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: RoleGroupMapSchema(many=True), dump_only=True)


class RolePaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: RoleGroupSchema(many=True), dump_only=True)


class TagPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: TagSchema(many=True), dump_only=True)


class UserPaginationSchema(PaginationResponseSchema):
    results = fields.Nested(lambda: OktaUserSchema(many=True), dump_only=True)
