"""
Test script for remaining Pydantic schemas (delete_message, role_memberships, metrics).
"""

import pytest
from datetime import datetime, timedelta

from api_v2.schemas.delete_message import DeleteMessage
from api_v2.schemas.role_memberships import RoleMemberRead, RoleMemberUpdate
from api_v2.schemas.metrics import Metrics, MetricsData, MetricType


def test_delete_message_schema():
    """Test delete message schema."""
    # DeleteMessage schema imported successfully

    delete_msg = DeleteMessage()
    # DeleteMessage: deleted={delete_msg.deleted}
    assert delete_msg.deleted is True

    delete_msg_explicit = DeleteMessage(deleted=True)
    # DeleteMessage explicit: deleted={delete_msg_explicit.deleted}
    assert delete_msg_explicit.deleted is True


def test_role_membership_schemas():
    """Test role membership schemas."""
    # Role membership schemas imported successfully

    future_date = datetime.now() + timedelta(days=30)
    valid_group_ids = ["12345678901234567890", "09876543210987654321"]  # 20 chars each

    role_update = RoleMemberUpdate(
        groups_to_add=valid_group_ids,
        groups_should_expire=[0],
        owner_groups_to_add=[valid_group_ids[1]],
        groups_to_remove=[],
        owner_groups_should_expire=[],
        owner_groups_to_remove=[],
        created_reason="Adding groups for role expansion",
        groups_added_ending_at=future_date,
    )
    # RoleMemberUpdate: adding {len(role_update.groups_to_add)} groups
    assert len(role_update.groups_to_add) == 2

    role_read = RoleMemberRead(groups_in_role=valid_group_ids, groups_owned_by_role=[valid_group_ids[0]])
    # RoleMemberRead: {len(role_read.groups_in_role)} groups in role
    assert len(role_read.groups_in_role) == 2


def test_role_validation_invalid_group_id():
    """Test role validation - invalid group ID length."""
    with pytest.raises(ValueError):
        invalid_role_update = RoleMemberUpdate(
            groups_to_add=["short"], owner_groups_to_add=[], groups_to_remove=[], owner_groups_to_remove=[]
        )
        # Role validation correctly rejected short group ID


def test_role_validation_past_date():
    """Test role validation - past date should fail."""
    valid_group_ids = ["12345678901234567890", "09876543210987654321"]
    past_date = datetime.now() - timedelta(days=1)
    
    with pytest.raises(ValueError):
        invalid_role_update = RoleMemberUpdate(
            groups_to_add=valid_group_ids,
            owner_groups_to_add=[],
            groups_to_remove=[],
            owner_groups_to_remove=[],
            groups_added_ending_at=past_date,
        )
        # Role validation correctly rejected past ending date


def test_metrics_schemas():
    """Test metrics schemas."""
    # Metrics schemas imported successfully

    # MetricType enum values: {[t.value for t in MetricType]}
    enum_values = [t.value for t in MetricType]
    assert len(enum_values) == 4  # Based on the original test expectation

    # Test MetricsData with value
    metrics_data_value = MetricsData(
        name="api_requests_total", value=42.5, tags={"endpoint": "/api/users", "method": "GET"}
    )
    # MetricsData with value: {metrics_data_value.name} = {metrics_data_value.value}
    assert metrics_data_value.name == "api_requests_total"
    assert metrics_data_value.value == 42.5

    # Test MetricsData with duration
    metrics_data_duration = MetricsData(name="request_duration", duration=0.123, tags={"service": "auth"})
    # MetricsData with duration: {metrics_data_duration.duration}s
    assert metrics_data_duration.duration == 0.123

    # Test MetricsData with buckets (histogram)
    metrics_data_histogram = MetricsData(name="response_times", value=1.5, buckets=[0.1, 0.5, 1.0, 2.0, 5.0])
    # MetricsData with histogram: {len(metrics_data_histogram.buckets)} buckets
    assert len(metrics_data_histogram.buckets) == 5

    # Test Metrics complete schema
    counter_metric = Metrics(type=MetricType.COUNTER, data=metrics_data_value)
    # Counter metric: {counter_metric.type}
    assert counter_metric.type == MetricType.COUNTER

    timing_metric = Metrics(type=MetricType.TIMING, data=metrics_data_duration)
    # Timing metric: {timing_metric.type}
    assert timing_metric.type == MetricType.TIMING

    histogram_metric = Metrics(type=MetricType.HISTOGRAM, data=metrics_data_histogram)
    # Histogram metric: {histogram_metric.type}
    assert histogram_metric.type == MetricType.HISTOGRAM


def test_metrics_validation_missing_value_duration():
    """Test metrics validation - missing both value and duration should fail."""
    with pytest.raises(ValueError):
        invalid_metrics_data = MetricsData(
            name="invalid_metric",
            tags={"test": "true"},
            # No value or duration
        )
        # Metrics validation correctly rejected missing value/duration


def test_metrics_validation_long_name():
    """Test metrics validation - name too long should fail."""
    long_name = "x" * 256  # Too long (max 255)
    
    with pytest.raises(ValueError):
        invalid_metrics_data = MetricsData(name=long_name, value=1.0)
        # Metrics validation correctly rejected long name


def test_json_serialization():
    """Test JSON serialization for all schemas."""
    delete_msg = DeleteMessage()
    
    future_date = datetime.now() + timedelta(days=30)
    valid_group_ids = ["12345678901234567890", "09876543210987654321"]
    
    role_update = RoleMemberUpdate(
        groups_to_add=valid_group_ids,
        groups_should_expire=[0],
        owner_groups_to_add=[valid_group_ids[1]],
        groups_to_remove=[],
        owner_groups_should_expire=[],
        owner_groups_to_remove=[],
        created_reason="Adding groups for role expansion",
        groups_added_ending_at=future_date,
    )
    
    metrics_data_value = MetricsData(
        name="api_requests_total", value=42.5, tags={"endpoint": "/api/users", "method": "GET"}
    )
    counter_metric = Metrics(type=MetricType.COUNTER, data=metrics_data_value)
    
    delete_json = delete_msg.model_dump()
    role_json = role_update.model_dump()
    metrics_json = counter_metric.model_dump()
    
    # JSON serialization works for all schemas
    assert isinstance(delete_json, dict)
    assert isinstance(role_json, dict)
    assert isinstance(metrics_json, dict)


def test_minimal_schemas():
    """Test with minimal data."""
    minimal_role = RoleMemberUpdate(
        groups_to_add=[], owner_groups_to_add=[], groups_to_remove=[], owner_groups_to_remove=[]
    )
    minimal_metrics_data = MetricsData(value=1.0)
    minimal_metrics = Metrics(type=MetricType.GAUGE, data=minimal_metrics_data)
    
    # Minimal schemas work correctly
    assert minimal_role.groups_to_add == []
    assert minimal_metrics_data.value == 1.0
    assert minimal_metrics.type == MetricType.GAUGE

    # All remaining schemas converted successfully!
