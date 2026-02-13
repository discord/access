from datetime import datetime, timezone
from typing import Any, Dict, Optional

from marshmallow import Schema, ValidationError, fields, post_load, validate


class CreateGroupRequestSchema(Schema):
    group_owner = fields.Boolean(load_default=False, load_only=True)
    requested_group_name = fields.String(validate=validate.Length(min=1, max=255), required=True, load_only=True)
    requested_group_description = fields.String(validate=validate.Length(max=1024), required=False, load_only=True, load_default="")
    requested_group_type = fields.String(
        required=True,
        load_only=True,
        validate=validate.OneOf(["okta_group", "role_group", "app_group"])
    )
    requested_app_id = fields.String(validate=validate.Length(equal=20), required=False, allow_none=True, load_only=True)
    requested_group_tags = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True, load_default=[])
    request_reason = fields.String(validate=validate.Length(max=1024), load_only=True, load_default="")


class ResolveGroupRequestSchema(Schema):
    approved = fields.Boolean(required=True, load_only=True)
    resolved_group_name = fields.String(validate=validate.Length(min=1, max=255), required=False, load_only=True)
    resolved_group_description = fields.String(validate=validate.Length(max=1024), required=False, load_only=True)
    resolved_group_type = fields.String(
        required=False,
        load_only=True,
        validate=validate.OneOf(["okta_group", "role_group", "app_group"])
    )
    resolved_app_id = fields.String(validate=validate.Length(equal=20), required=False, allow_none=True, load_only=True)
    resolved_group_tags = fields.List(fields.String(validate=validate.Length(equal=20)), load_only=True)
    resolution_reason = fields.String(load_only=True, validate=validate.Length(max=1024), load_default="")
