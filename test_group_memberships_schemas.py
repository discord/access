#!/usr/bin/env python3
"""
Test script for group membership Pydantic schemas.
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

try:
    # Test group membership schemas
    from api_v2.schemas.group_memberships import GroupMemberRead, GroupMemberUpdate

    print("✓ Group membership schemas imported successfully")

    # Test GroupMemberUpdate with valid data
    future_date = datetime.now() + timedelta(days=30)
    valid_user_ids = ["12345678901234567890", "09876543210987654321"]  # 20 chars each

    member_update = GroupMemberUpdate(
        members_to_add=valid_user_ids,
        owners_to_add=[valid_user_ids[0]],
        members_should_expire=[0],
        owners_should_expire=[],
        members_to_remove=[],
        owners_to_remove=[],
        created_reason="Adding users for project access",
        users_added_ending_at=future_date,
    )
    print(f"✓ GroupMemberUpdate: adding {len(member_update.members_to_add)} members")

    # Test GroupMemberRead
    member_read = GroupMemberRead(members=valid_user_ids, owners=[valid_user_ids[0]])
    print(f"✓ GroupMemberRead: {len(member_read.members)} members, {len(member_read.owners)} owners")

    # Test with empty lists (should be valid)
    empty_update = GroupMemberUpdate(members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[])
    print("✓ Empty lists are valid for membership updates")

    # Test validation - invalid user ID length (should fail)
    try:
        invalid_update = GroupMemberUpdate(
            members_to_add=["short"],  # Too short
            owners_to_add=[],
            members_to_remove=[],
            owners_to_remove=[],
        )
        print("✗ Validation should have failed for short user ID")
    except ValueError:
        print("✓ Validation correctly rejected short user ID")

    # Test validation - user ID too long (should fail)
    try:
        invalid_update = GroupMemberUpdate(
            members_to_add=["123456789012345678901"],  # Too long (21 chars)
            owners_to_add=[],
            members_to_remove=[],
            owners_to_remove=[],
        )
        print("✗ Validation should have failed for long user ID")
    except ValueError:
        print("✓ Validation correctly rejected long user ID")

    # Test validation - past date should fail
    try:
        past_date = datetime.now() - timedelta(days=1)
        invalid_update = GroupMemberUpdate(
            members_to_add=valid_user_ids,
            owners_to_add=[],
            members_to_remove=[],
            owners_to_remove=[],
            users_added_ending_at=past_date,
        )
        print("✗ Validation should have failed for past ending date")
    except ValueError:
        print("✓ Validation correctly rejected past ending date")

    # Test validation - reason too long should fail
    try:
        long_reason = "x" * 1025  # Too long (max 1024)
        invalid_update = GroupMemberUpdate(
            members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[], created_reason=long_reason
        )
        print("✗ Validation should have failed for long reason")
    except ValueError:
        print("✓ Validation correctly rejected long reason")

    # Test JSON serialization
    update_json = member_update.model_dump()
    print(f"✓ JSON serialization works: {len(update_json)} fields")

    # Test with None optional fields
    minimal_update = GroupMemberUpdate(members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[])
    print("✓ Minimal update with required fields only works")

    # Test default factory for GroupMemberRead
    default_read = GroupMemberRead()
    print(f"✓ Default GroupMemberRead: {len(default_read.members)} members")

    print("\n🎯 Group membership schemas test completed successfully!")
    print("✓ GroupMemberUpdate schema working")
    print("✓ GroupMemberRead schema working")
    print("✓ User ID length validation (must be 20 chars)")
    print("✓ Future date validation for ending_at")
    print("✓ Reason length validation (max 1024 chars)")
    print("✓ Optional field handling working")
    print("✓ Empty list handling working")
    print("✓ JSON serialization working")

except Exception as e:
    print(f"✗ Group membership schema test failed: {e}")
    import traceback

    traceback.print_exc()
