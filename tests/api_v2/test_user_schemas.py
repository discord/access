"""
Test script for User Pydantic schemas.
"""

import pytest
from api_v2.schemas import UserDetail, UserSearch, UserSummary


def test_user_schemas():
    """Test User Pydantic schemas."""
    # Test UserSummary
    user_summary = UserSummary(
        id="user123", email="wumpus@discord.com", first_name="Test", last_name="User", display_name="Test User"
    )
    assert user_summary.email == "wumpus@discord.com"

    # Test UserDetail
    user_detail = UserDetail(
        id="user456",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        email="detail@example.com",
        first_name="Detail",
        last_name="User",
        display_name="Detail User",
        manager=user_summary,
    )
    assert user_detail.email == "detail@example.com"
    assert user_detail.manager.email == "wumpus@discord.com"

    # Test UserSearch
    user_search = UserSearch(q="test query", page=2, per_page=50)
    assert user_search.page == 2
    assert user_search.per_page == 50

    # Test validation
    with pytest.raises(ValueError):
        UserSearch(page=0)  # Should fail
