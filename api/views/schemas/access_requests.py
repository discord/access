from datetime import datetime, timezone
from typing import Any, Dict, Optional

from marshmallow import Schema, ValidationError, fields, post_load, validate, validates

from api.access_config import get_access_config


class CreateAccessRequestSchema(Schema):
    group_id = fields.String(validate=validate.Length(equal=20), required=True, load_only=True)
    group_owner = fields.Boolean(load_default=False, load_only=True)
    reason = fields.String(validate=validate.Length(max=1024), load_only=True)

    @validates("reason")
    def validate_reason(self, reason: str) -> None:
        access_config = get_access_config()

        # Check if reason is empty or only whitespace
        if not reason or not reason.strip():
            raise ValidationError("Reason is required")

        # Check if reason is the same as the template
        if access_config.reason_template and reason.strip() == access_config.reason_template.strip():
            raise ValidationError(
                "Please fill out the template with your specific information instead of submitting the template as-is."
            )

        # Check if required template fields are present
        if access_config.reason_template_required and reason:
            for required_field in access_config.reason_template_required:
                if required_field not in reason:
                    raise ValidationError(
                        f"The following required field is missing from your reason: {required_field}. Please fill it out before submitting your access request."
                    )

    @staticmethod
    def must_be_in_the_future(data: Optional[datetime]) -> None:
        if data and data < datetime.now():
            raise ValidationError("Ended at datetime for access request approval must be in the future")

    ending_at = fields.DateTime(
        load_only=True,
        format="rfc822",
        metadata={"validation": must_be_in_the_future},
    )

    @post_load
    def convert_to_utc(self, item: Dict[str, Any], many: bool, **kwargs: Any) -> Dict[str, Any]:
        # Ensure the datetime we store in the database is UTC
        if "ending_at" in item:
            item["ending_at"] = item["ending_at"].astimezone(tz=timezone.utc)
        return item


class ResolveAccessRequestSchema(Schema):
    approved = fields.Boolean(required=True, load_only=True)
    reason = fields.String(load_only=True, validate=validate.Length(max=1024))

    @staticmethod
    def must_be_in_the_future(data: Optional[datetime]) -> None:
        if data and data < datetime.now():
            raise ValidationError("Ended at datetime for access request approval must be in the future")

    ending_at = fields.DateTime(
        load_only=True,
        format="rfc822",
        metadata={"validation": must_be_in_the_future},
    )

    @post_load
    def convert_to_utc(self, item: Dict[str, Any], many: bool, **kwargs: Any) -> Dict[str, Any]:
        # Ensure the datetime we store in the database is UTC
        if "ending_at" in item:
            item["ending_at"] = item["ending_at"].astimezone(tz=timezone.utc)
        return item
