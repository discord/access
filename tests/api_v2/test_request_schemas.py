#!/usr/bin/env python3
"""
Test script for AccessRequest and RoleRequest Pydantic schemas.
"""

import pytest
from datetime import datetime, timedelta

# Test request schemas
from api_v2.schemas.requests import (
    AccessRequestCreate,
    AccessRequestResolve,
    AccessRequestSearch,
    AccessRequestStatus,
    RoleRequestCreate,
    RoleRequestSearch,
)


def test_request_schemas():
    """Test AccessRequest and RoleRequest Pydantic schemas."""
    # Test AccessRequestStatus enum
    status_values = [status.value for status in AccessRequestStatus]
    assert len(status_values) > 0, "Status enum should have values"

    # Test AccessRequestCreate schema
    future_date = datetime.now() + timedelta(days=30)
    access_request_data = {
        "group_id": "12345678901234567890",  # 20 chars
        "group_owner": True,
        "reason": "Need access for project work",
        "ending_at": future_date.isoformat(),
    }

    access_request = AccessRequestCreate(**access_request_data)
    assert access_request.group_id == "12345678901234567890"
    assert access_request.group_owner is True

    # Test RoleRequestCreate schema
    role_request_data = {
        "role_id": "98765432109876543210",  # 20 chars
        "group_id": "12345678901234567890",  # 20 chars
        "group_owner": False,
        "reason": "Role-based access needed",
        "ending_at": future_date.isoformat(),
    }

    role_request = RoleRequestCreate(**role_request_data)
    assert role_request.role_id == "98765432109876543210"
    assert role_request.group_id == "12345678901234567890"

    # Test AccessRequestResolve schema
    resolve_data = {"approved": True, "reason": "Request approved after review", "ending_at": future_date.isoformat()}

    resolve_request = AccessRequestResolve(**resolve_data)
    assert resolve_request.approved is True

    # Test validation - past date should fail
    with pytest.raises(ValueError):
        past_date = datetime.now() - timedelta(days=1)
        AccessRequestCreate(group_id="12345678901234567890", ending_at=past_date)

    # Test validation - invalid group_id length
    with pytest.raises(ValueError):
        AccessRequestCreate(
            group_id="short",  # Too short
            reason="Test",
        )

    # Test search schemas
    access_search = AccessRequestSearch(
        status=AccessRequestStatus.PENDING, requester_user_id="user123", page=1, per_page=25
    )
    assert access_search.status == AccessRequestStatus.PENDING

    role_search = RoleRequestSearch(
        status=AccessRequestStatus.APPROVED, requester_role_id="role123", page=2, per_page=10
    )
    assert role_search.status == AccessRequestStatus.APPROVED

    # Test JSON serialization
    access_json = access_request.model_dump()
    assert len(access_json) > 0, "JSON serialization should produce fields"
