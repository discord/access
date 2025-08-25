"""
Test script for group membership Pydantic schemas.
"""

import pytest
from datetime import datetime, timedelta
from api_v2.schemas.group_memberships import GroupMemberRead, GroupMemberUpdate


def test_group_membership_schemas():
    """Test group membership Pydantic schemas."""
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
    assert len(member_update.members_to_add) == 2

    # Test GroupMemberRead
    member_read = GroupMemberRead(members=valid_user_ids, owners=[valid_user_ids[0]])
    assert len(member_read.members) == 2
    assert len(member_read.owners) == 1

    # Test with empty lists (should be valid)
    empty_update = GroupMemberUpdate(members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[])
    assert len(empty_update.members_to_add) == 0

    # Test validation - invalid user ID length (should fail)
    with pytest.raises(ValueError):
        GroupMemberUpdate(
            members_to_add=["short"],  # Too short
            owners_to_add=[],
            members_to_remove=[],
            owners_to_remove=[],
        )

    # Test validation - user ID too long (should fail)
    with pytest.raises(ValueError):
        GroupMemberUpdate(
            members_to_add=["123456789012345678901"],  # Too long (21 chars)
            owners_to_add=[],
            members_to_remove=[],
            owners_to_remove=[],
        )

    # Test validation - past date should fail
    with pytest.raises(ValueError):
        past_date = datetime.now() - timedelta(days=1)
        GroupMemberUpdate(
            members_to_add=valid_user_ids,
            owners_to_add=[],
            members_to_remove=[],
            owners_to_remove=[],
            users_added_ending_at=past_date,
        )

    # Test validation - reason too long should fail
    with pytest.raises(ValueError):
        long_reason = "x" * 1025  # Too long (max 1024)
        GroupMemberUpdate(
            members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[], created_reason=long_reason
        )

    # Test JSON serialization
    update_json = member_update.model_dump()
    assert len(update_json) > 0, "JSON serialization should produce fields"

    # Test with None optional fields
    minimal_update = GroupMemberUpdate(members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[])
    assert len(minimal_update.members_to_add) == 0

    # Test default factory for GroupMemberRead
    default_read = GroupMemberRead()
    assert len(default_read.members) == 0
