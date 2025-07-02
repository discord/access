from typing import Any, Dict

from marshmallow import Schema, ValidationError, fields, validate, validates_schema


class MetricsDataSchema(Schema):
    """Schema for the data field of metrics."""

    name = fields.String(required=False, validate=validate.Length(min=1, max=255))
    value = fields.Float(required=False)
    duration = fields.Float(required=False)
    tags = fields.Dict(keys=fields.String(), values=fields.String(), required=False)
    buckets = fields.List(fields.Float(), required=False)

    @validates_schema
    def validate_data(self, data: Dict[str, Any], **kwargs: Any) -> None:
        # Ensure at least one value field is present
        if not any(key in data for key in ["value", "duration"]):
            raise ValidationError("Either 'value' or 'duration' must be provided in data")


class MetricsSchema(Schema):
    # Metrics matching operations exposed from metrics reporter hook
    type = fields.String(
        required=True,
        validate=validate.OneOf(["counter", "gauge", "histogram", "timing"]),
        metadata={"description": "Type of metric to record"},
    )
    data = fields.Nested(
        MetricsDataSchema, required=True, metadata={"description": "Metric data including value and optional metadata"}
    )
