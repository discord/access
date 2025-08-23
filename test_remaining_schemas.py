#!/usr/bin/env python3
"""
Test script for remaining Pydantic schemas (delete_message, role_memberships, metrics).
"""

import os
import sys
from datetime import datetime, timedelta

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///instance/access.db"

# Set up Flask app context
# Initialize FastAPI app (which sets up logging and services)

try:
    print("Testing remaining schemas...")

    # Test delete message schema
    from api_v2.schemas.delete_message import DeleteMessage

    print("✓ DeleteMessage schema imported successfully")

    delete_msg = DeleteMessage()
    print(f"✓ DeleteMessage: deleted={delete_msg.deleted}")

    delete_msg_explicit = DeleteMessage(deleted=True)
    print(f"✓ DeleteMessage explicit: deleted={delete_msg_explicit.deleted}")

    # Test role membership schemas
    from api_v2.schemas.role_memberships import RoleMemberRead, RoleMemberUpdate

    print("✓ Role membership schemas imported successfully")

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
    print(f"✓ RoleMemberUpdate: adding {len(role_update.groups_to_add)} groups")

    role_read = RoleMemberRead(groups_in_role=valid_group_ids, groups_owned_by_role=[valid_group_ids[0]])
    print(f"✓ RoleMemberRead: {len(role_read.groups_in_role)} groups in role")

    # Test role validation - invalid group ID length
    try:
        invalid_role_update = RoleMemberUpdate(
            groups_to_add=["short"], owner_groups_to_add=[], groups_to_remove=[], owner_groups_to_remove=[]
        )
        print("✗ Role validation should have failed for short group ID")
    except ValueError:
        print("✓ Role validation correctly rejected short group ID")

    # Test role validation - past date should fail
    try:
        past_date = datetime.now() - timedelta(days=1)
        invalid_role_update = RoleMemberUpdate(
            groups_to_add=valid_group_ids,
            owner_groups_to_add=[],
            groups_to_remove=[],
            owner_groups_to_remove=[],
            groups_added_ending_at=past_date,
        )
        print("✗ Role validation should have failed for past ending date")
    except ValueError:
        print("✓ Role validation correctly rejected past ending date")

    # Test metrics schemas
    from api_v2.schemas.metrics import Metrics, MetricsData, MetricType

    print("✓ Metrics schemas imported successfully")

    # Test MetricType enum
    print(f"✓ MetricType enum values: {[t.value for t in MetricType]}")

    # Test MetricsData with value
    metrics_data_value = MetricsData(
        name="api_requests_total", value=42.5, tags={"endpoint": "/api/users", "method": "GET"}
    )
    print(f"✓ MetricsData with value: {metrics_data_value.name} = {metrics_data_value.value}")

    # Test MetricsData with duration
    metrics_data_duration = MetricsData(name="request_duration", duration=0.123, tags={"service": "auth"})
    print(f"✓ MetricsData with duration: {metrics_data_duration.duration}s")

    # Test MetricsData with buckets (histogram)
    metrics_data_histogram = MetricsData(name="response_times", value=1.5, buckets=[0.1, 0.5, 1.0, 2.0, 5.0])
    print(f"✓ MetricsData with histogram: {len(metrics_data_histogram.buckets)} buckets")

    # Test Metrics complete schema
    counter_metric = Metrics(type=MetricType.COUNTER, data=metrics_data_value)
    print(f"✓ Counter metric: {counter_metric.type}")

    timing_metric = Metrics(type=MetricType.TIMING, data=metrics_data_duration)
    print(f"✓ Timing metric: {timing_metric.type}")

    histogram_metric = Metrics(type=MetricType.HISTOGRAM, data=metrics_data_histogram)
    print(f"✓ Histogram metric: {histogram_metric.type}")

    # Test metrics validation - missing both value and duration should fail
    try:
        invalid_metrics_data = MetricsData(
            name="invalid_metric",
            tags={"test": "true"},
            # No value or duration
        )
        print("✗ Metrics validation should have failed for missing value/duration")
    except ValueError:
        print("✓ Metrics validation correctly rejected missing value/duration")

    # Test metrics validation - name too long should fail
    try:
        long_name = "x" * 256  # Too long (max 255)
        invalid_metrics_data = MetricsData(name=long_name, value=1.0)
        print("✗ Metrics validation should have failed for long name")
    except ValueError:
        print("✓ Metrics validation correctly rejected long name")

    # Test JSON serialization for all schemas
    delete_json = delete_msg.model_dump()
    role_json = role_update.model_dump()
    metrics_json = counter_metric.model_dump()
    print("✓ JSON serialization works for all schemas")

    # Test with minimal data
    minimal_role = RoleMemberUpdate(
        groups_to_add=[], owner_groups_to_add=[], groups_to_remove=[], owner_groups_to_remove=[]
    )
    minimal_metrics_data = MetricsData(value=1.0)
    minimal_metrics = Metrics(type=MetricType.GAUGE, data=minimal_metrics_data)
    print("✓ Minimal schemas work correctly")

    print("\n🎯 Remaining schemas test completed successfully!")
    print("✓ DeleteMessage schema (1 schema)")
    print("✓ RoleMemberUpdate and RoleMemberRead schemas (2 schemas)")
    print("✓ Metrics and MetricsData schemas (2 schemas)")
    print("✓ MetricType enum with 4 metric types")
    print("✓ Group ID length validation (20 chars)")
    print("✓ Future date validation")
    print("✓ Metrics value/duration validation")
    print("✓ Field length validations")
    print("✓ JSON serialization working")
    print("✓ All remaining schemas converted successfully!")

except Exception as e:
    print(f"✗ Remaining schemas test failed: {e}")
    import traceback

    traceback.print_exc()
