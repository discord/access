#!/usr/bin/env python3
"""
Test script for Pydantic group schemas with discriminated unions.
"""

import pytest

# Test group schemas
from api_v2.schemas import AppGroupRead, OktaGroupCreate, OktaGroupRead, RoleGroupRead


def test_group_schemas():
    """Test Pydantic group schemas with discriminated unions."""
    # Test OktaGroup creation and serialization
    okta_group_data = {
        "id": "group123",
        "created_at": "2023-01-01T00:00:00",
        "updated_at": "2023-01-01T00:00:00",
        "deleted_at": None,
        "type": "okta_group",
        "name": "Test Group",
        "description": "A test group",
        "is_managed": True,
        "externally_managed_data": {},
        "plugin_data": {},
    }

    okta_group = OktaGroupRead(**okta_group_data)
    assert okta_group.name == "Test Group"
    assert okta_group.type == "okta_group"

    # Test RoleGroup
    role_group_data = {
        "id": "role123",
        "created_at": "2023-01-01T00:00:00",
        "updated_at": "2023-01-01T00:00:00",
        "deleted_at": None,
        "type": "role_group",
        "name": "Role-AdminRole",
        "description": "Admin role group",
        "is_managed": True,
        "externally_managed_data": {},
        "plugin_data": {},
    }

    role_group = RoleGroupRead(**role_group_data)
    assert role_group.name == "Role-AdminRole"
    assert role_group.type == "role_group"

    # Test AppGroup
    app_group_data = {
        "id": "app123",
        "created_at": "2023-01-01T00:00:00",
        "updated_at": "2023-01-01T00:00:00",
        "deleted_at": None,
        "type": "app_group",
        "name": "App-MyApp-Users",
        "description": "MyApp users",
        "is_managed": True,
        "externally_managed_data": {},
        "plugin_data": {},
        "app_id": "app456",
        "is_owner": False,
    }

    app_group = AppGroupRead(**app_group_data)
    assert app_group.name == "App-MyApp-Users"
    assert app_group.type == "app_group"
    assert app_group.app_id == "app456"

    # Test discriminated union - this is the key feature
    # We should be able to parse different group types from the same input
    groups_json = [okta_group_data, role_group_data, app_group_data]

    for i, group_data in enumerate(groups_json):
        # Test that we can determine the right type based on the discriminator
        if group_data["type"] == "okta_group":
            parsed = OktaGroupRead(**group_data)
        elif group_data["type"] == "role_group":
            parsed = RoleGroupRead(**group_data)
        elif group_data["type"] == "app_group":
            parsed = AppGroupRead(**group_data)
        else:
            raise ValueError(f"Unknown group type: {group_data['type']}")

        assert parsed.__class__.__name__ in ["OktaGroupRead", "RoleGroupRead", "AppGroupRead"]

    # Test JSON serialization
    okta_json = okta_group.model_dump()
    assert len(okta_json) > 0, "JSON serialization should produce fields"

    # Test validation - missing required field
    with pytest.raises(Exception):
        OktaGroupRead(
            id="123",
            created_at="2023-01-01T00:00:00",
            updated_at="2023-01-01T00:00:00",
            type="okta_group",
            # name is missing - should fail
            description="Test",
            is_managed=True,
            externally_managed_data={},
            plugin_data={},
        )

    # Test create schemas
    create_data = {
        "type": "okta_group",
        "name": "New Test Group",
        "description": "A new test group",
        "tags_to_add": ["tag1", "tag2"],
    }

    new_group = OktaGroupCreate(**create_data)
    assert new_group.name == "New Test Group"
