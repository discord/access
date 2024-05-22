from datetime import datetime, timezone
from typing import Any, Dict, Optional

from marshmallow import Schema, ValidationError, fields, post_load, validate


class CreateAccessRequestSchema(Schema):
    group_id = fields.String(validate=validate.Length(equal=20), required=True, load_only=True)
    group_owner = fields.Boolean(load_default=False, load_only=True)
    reason = fields.String(validate=validate.Length(max=1024), load_only=True)

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
