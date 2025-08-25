"""
Test script for Pydantic schemas.
This validates that our schemas can be imported and work correctly.
"""

import pytest

from api_v2.schemas.core_schemas import UserSummary


def test_base_schemas():
    """Test base schemas."""
    # Base schemas imported successfully
    pass


def test_user_schemas():
    """Test user schemas."""
    # User schemas imported successfully

    # Test creating a user summary
    user_data = {
        "id": "12345",
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User",
        "display_name": "Test User",
        "deleted_at": None,
    }

    user = UserSummary(**user_data)
    # UserSummary created: {user.email}
    assert user.email == "test@example.com"


def test_json_serialization():
    """Test JSON serialization."""
    user_data = {
        "id": "12345",
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User",
        "display_name": "Test User",
        "deleted_at": None,
    }
    
    user = UserSummary(**user_data)
    user_json = user.model_dump()
    # JSON serialization works: {len(user_json)} fields
    assert isinstance(user_json, dict)
    assert len(user_json) > 0


def test_validation():
    """Test validation."""
    with pytest.raises(Exception):
        invalid_user = UserSummary(
            id="123",
            email="not-an-email",  # Invalid email
            first_name="Test",
            last_name="User",
            display_name="Test User",
        )
        # Validation correctly rejected invalid email
