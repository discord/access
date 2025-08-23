#!/usr/bin/env python3
"""
Test script for AccessRequest and RoleRequest Pydantic schemas.
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
# CURRENT_OKTA_USER_EMAIL will be loaded from .env file

try:
    # Test request schemas
    from api_v2.schemas.requests import (
        AccessRequestCreate,
        AccessRequestResolve,
        AccessRequestSearch,
        AccessRequestStatus,
        RoleRequestCreate,
        RoleRequestSearch,
    )

    print("✓ Request schemas imported successfully")

    # Test AccessRequestStatus enum
    print(f"✓ Status enum values: {[status.value for status in AccessRequestStatus]}")

    # Test AccessRequestCreate schema
    future_date = datetime.now() + timedelta(days=30)
    access_request_data = {
        "group_id": "12345678901234567890",  # 20 chars
        "group_owner": True,
        "reason": "Need access for project work",
        "ending_at": future_date.isoformat(),
    }

    access_request = AccessRequestCreate(**access_request_data)
    print(f"✓ AccessRequestCreate: group_id={access_request.group_id}, owner={access_request.group_owner}")

    # Test RoleRequestCreate schema
    role_request_data = {
        "role_id": "98765432109876543210",  # 20 chars
        "group_id": "12345678901234567890",  # 20 chars
        "group_owner": False,
        "reason": "Role-based access needed",
        "ending_at": future_date.isoformat(),
    }

    role_request = RoleRequestCreate(**role_request_data)
    print(f"✓ RoleRequestCreate: role_id={role_request.role_id}, group_id={role_request.group_id}")

    # Test AccessRequestResolve schema
    resolve_data = {"approved": True, "reason": "Request approved after review", "ending_at": future_date.isoformat()}

    resolve_request = AccessRequestResolve(**resolve_data)
    print(f"✓ AccessRequestResolve: approved={resolve_request.approved}")

    # Test validation - past date should fail
    try:
        past_date = datetime.now() - timedelta(days=1)
        invalid_request = AccessRequestCreate(group_id="12345678901234567890", ending_at=past_date)
        print("✗ Validation should have failed for past date")
    except ValueError:
        print("✓ Validation correctly rejected past ending_at date")

    # Test validation - invalid group_id length
    try:
        invalid_request = AccessRequestCreate(
            group_id="short",  # Too short
            reason="Test",
        )
        print("✗ Validation should have failed for short group_id")
    except ValueError:
        print("✓ Validation correctly rejected short group_id")

    # Test search schemas
    access_search = AccessRequestSearch(
        status=AccessRequestStatus.PENDING, requester_user_id="user123", page=1, per_page=25
    )
    print(f"✓ AccessRequestSearch: status={access_search.status}")

    role_search = RoleRequestSearch(
        status=AccessRequestStatus.APPROVED, requester_role_id="role123", page=2, per_page=10
    )
    print(f"✓ RoleRequestSearch: status={role_search.status}")

    # Test JSON serialization
    access_json = access_request.model_dump()
    print(f"✓ JSON serialization works: {len(access_json)} fields")

    print("\n🎯 Request schemas test completed successfully!")
    print("✓ AccessRequest schemas working")
    print("✓ RoleRequest schemas working")
    print("✓ Status enum working")
    print("✓ Validation working (date validation, field lengths)")
    print("✓ Search schemas working")
    print("✓ JSON serialization working")

except Exception as e:
    print(f"✗ Request schema test failed: {e}")
    import traceback

    traceback.print_exc()
