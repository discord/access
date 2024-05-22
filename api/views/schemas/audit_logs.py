from enum import Enum
from typing import Any, Dict

from marshmallow import Schema, fields, pre_dump

from api.views.schemas.core_schemas import (
    AccessRequestSchema,
    AppSchema,
    OktaUserSchema,
    PolymorphicGroupSchema,
    RoleGroupSchema,
    TagSchema,
)


class EventType(Enum):
    access_approve = "ACCESS_APPROVE"
    access_create = "ACCESS_CREATE"
    access_reject = "ACCESS_REJECT"
    app_create = "APP_CREATE"
    app_delete = "APP_DELETE"
    app_modify_name = "APP_MODIFY_NAME"
    app_modify_tags = "APP_MODIFY_TAG"
    group_create = "GROUP_CREATE"
    group_delete = "GROUP_DELETE"
    group_modify_name = "GROUP_MODIFY_NAME"
    group_modify_type = "GROUP_MODIFY_TYPE"
    group_modify_tags = "GROUP_MODIFY_TAG"
    group_modify_users = "GROUP_MODIFY_USER"
    role_group_modify = "ROLE_GROUP_MODIFY"
    tag_create = "TAG_CREATE"
    tag_modify = "TAG_MODIFY"
    tag_delete = "TAG_DELETE"


class AuditLogSchema(Schema):
    event_type = fields.Enum(EventType, by_value=True)
    user_agent = fields.Str()
    ip = fields.Str()
    current_user_id = fields.Str()
    current_user_email = fields.Str()

    group = fields.Nested(PolymorphicGroupSchema, only=("id", "name", "type", "app.id", "app.name"))
    old_group_name = fields.Str()
    old_group_type = fields.Str()
    group_owners = fields.List(fields.Nested(OktaUserSchema, only=("id", "email")))
    owners_removed_ids_emails = fields.List(fields.Nested(OktaUserSchema, only=("id", "email")))
    owners_added_ids_emails = fields.List(fields.Nested(OktaUserSchema, only=("id", "email")))
    members_removed_ids_emails = fields.List(fields.Nested(OktaUserSchema, only=("id", "email")))
    members_added_ids_emails = fields.List(fields.Nested(OktaUserSchema, only=("id", "email")))

    role = fields.Nested(RoleGroupSchema, only=("id", "name"))
    groups_added_ending_at = fields.DateTime()
    owner_groups_removed_ids_names = fields.List(fields.Nested(RoleGroupSchema, only=("id", "name")))
    owner_groups_added_ids_names = fields.List(fields.Nested(RoleGroupSchema, only=("id", "name")))
    groups_removed_ids_names = fields.List(fields.Nested(RoleGroupSchema, only=("id", "name")))
    groups_added_ids_names = fields.List(fields.Nested(RoleGroupSchema, only=("id", "name")))

    request = fields.Nested(
        AccessRequestSchema,
        only=(
            "id",
            "request_reason",
            "request_ending_at",
            "request_ownership",
            "resolution_reason",
            "approval_ending_at",
        ),
    )
    requester = fields.Nested(OktaUserSchema, only=("id", "email"))

    app = fields.Nested(AppSchema, only=("id", "name"))
    old_app_name = fields.Str()
    owner_id = fields.Str()

    tag = fields.Nested(TagSchema, only=("id", "name", "constraints", "enabled"))
    old_tag = fields.Nested(TagSchema, only=("name", "constraints", "enabled"))

    tags_added = fields.List(fields.Nested(TagSchema, only=("id", "name", "constraints", "enabled")))
    tags_removed = fields.List(fields.Nested(TagSchema, only=("id", "name", "constraints", "enabled")))

    @pre_dump
    def remove_skip_values(self, data: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        allowed_keys = ["groups_added_ending_at", "current_user_id", "current_user_email"]
        return {key: value for key, value in data.items() if value is not None or key in allowed_keys}
