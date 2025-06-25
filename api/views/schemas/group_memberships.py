from datetime import datetime, timezone
from typing import Any, Dict, Optional

from marshmallow import Schema, ValidationError, fields, post_load, validate


class GroupMemberSchema(Schema):
    members = fields.List(fields.String(), dump_only=True)
    owners = fields.List(fields.String(), dump_only=True)

    members_to_add = fields.List(fields.String(validate=validate.Length(equal=20)), required=True, load_only=True)
    owners_to_add = fields.List(fields.String(validate=validate.Length(equal=20)), required=True, load_only=True)
    members_should_expire = fields.List(fields.Int(), required=False, load_only=True)
    owners_should_expire = fields.List(fields.Int(), required=False, load_only=True)
    members_to_remove = fields.List(fields.String(validate=validate.Length(equal=20)), required=True, load_only=True)
    owners_to_remove = fields.List(fields.String(validate=validate.Length(equal=20)), required=True, load_only=True)
    created_reason = fields.String(load_only=True, validate=validate.Length(max=1024))

    @staticmethod
    def must_be_in_the_future(data: Optional[datetime]) -> None:
        if data and data < datetime.now():
            raise ValidationError("Ended at datetime for add users must be in the future")

    users_added_ending_at = fields.DateTime(
        load_only=True, format="rfc822", metadata={"validation": must_be_in_the_future}
    )

    @post_load
    def convert_to_utc(self, item: Dict[str, Any], many: bool, **kwargs: Any) -> Dict[str, Any]:
        # Ensure the datetime we store in the database is UTC
        if "users_added_ending_at" in item:
            item["users_added_ending_at"] = item["users_added_ending_at"].astimezone(tz=timezone.utc)
        return item
