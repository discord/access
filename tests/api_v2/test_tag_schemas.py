"""
Test script for Tag Pydantic schemas.
"""

import pytest
from datetime import datetime
from api_v2.schemas.core_schemas import TagCreate, TagRead, TagSearch, TagUpdate


def test_tag_schemas():
    """Test Tag Pydantic schemas."""
    # Test TagRead
    tag_read = TagRead(
        id="tag123", created_at=datetime.now(), updated_at=datetime.now(), name="test-tag", enabled=True, constraints={}
    )
    assert tag_read.name == "test-tag"
    assert tag_read.enabled is True

    # Test TagCreate
    tag_create = TagCreate(name="new-tag", enabled=True, constraints={"department": "engineering"})
    assert tag_create.name == "new-tag"

    # Test TagUpdate
    tag_update = TagUpdate(name="updated-tag", enabled=False)
    assert tag_update.name == "updated-tag"
    assert tag_update.enabled is False

    # Test TagSearch
    tag_search = TagSearch(q="admin", page=1, per_page=25)
    assert tag_search.q == "admin"

    # Test validation
    with pytest.raises(ValueError):
        TagSearch(per_page=150)  # Should fail (max 100)
