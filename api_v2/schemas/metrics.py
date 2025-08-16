"""
FastAPI/Pydantic metrics schemas.
"""
from typing import Dict, List, Optional, Any
from pydantic import Field, field_validator, model_validator
from enum import Enum

from api_v2.schemas.base import BaseSchema


class MetricType(str, Enum):
    """Types of metrics that can be recorded."""
    COUNTER = "counter"
    GAUGE = "gauge"  
    HISTOGRAM = "histogram"
    TIMING = "timing"


class MetricsData(BaseSchema):
    """
    Schema for the data field of metrics.
    Maps to MetricsDataSchema from Marshmallow.
    """
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Metric name"
    )
    value: Optional[float] = Field(None, description="Metric value")
    duration: Optional[float] = Field(None, description="Duration in seconds")
    tags: Optional[Dict[str, str]] = Field(
        None,
        description="Additional tags for the metric"
    )
    buckets: Optional[List[float]] = Field(
        None,
        description="Histogram buckets for histogram metrics"
    )
    
    @model_validator(mode='after')
    def validate_required_fields(self) -> 'MetricsData':
        """Ensure at least one value field is present."""
        if not (self.value is not None or self.duration is not None):
            raise ValueError("Either 'value' or 'duration' must be provided in data")
        return self


class Metrics(BaseSchema):
    """
    Schema for metrics recording.
    Maps to MetricsSchema from Marshmallow.
    """
    type: MetricType = Field(..., description="Type of metric to record")
    data: MetricsData = Field(..., description="Metric data including value and optional metadata")