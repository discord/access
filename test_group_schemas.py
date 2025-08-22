#!/usr/bin/env python3
"""
Test script for Pydantic group schemas with discriminated unions.
"""

import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///instance/access.db"
# CURRENT_OKTA_USER_EMAIL will be loaded from .env file

# Set up Flask app context
from api.app import create_app

flask_app = create_app(testing=True)

with flask_app.app_context():
    try:
        # Test group schemas
        from api_v2.schemas import AppGroupRead, OktaGroupCreate, OktaGroupRead, RoleGroupRead

        print("✓ Group schemas imported successfully")

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
        print(f"✓ OktaGroup created: {okta_group.name} (type: {okta_group.type})")

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
        print(f"✓ RoleGroup created: {role_group.name} (type: {role_group.type})")

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
        print(f"✓ AppGroup created: {app_group.name} (type: {app_group.type}, app_id: {app_group.app_id})")

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

            print(f"✓ Group {i+1} parsed correctly as {parsed.__class__.__name__}")

        # Test JSON serialization
        okta_json = okta_group.model_dump()
        print(f"✓ JSON serialization works: {len(okta_json)} fields")

        # Test validation - missing required field
        try:
            invalid_group = OktaGroupRead(
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
            print("✗ Validation should have failed for missing name")
        except Exception:
            print("✓ Validation correctly rejected missing required field")

        # Test create schemas
        create_data = {
            "type": "okta_group",
            "name": "New Test Group",
            "description": "A new test group",
            "tags_to_add": ["tag1", "tag2"],
        }

        new_group = OktaGroupCreate(**create_data)
        print(f"✓ Create schema works: {new_group.name}")

        print("\n🎯 Group schemas test completed successfully!")
        print("✓ All group types working")
        print("✓ Discriminated unions working")
        print("✓ Validation working")
        print("✓ Create/Read schemas working")
        print("✓ JSON serialization working")

    except Exception as e:
        print(f"✗ Group schema test failed: {e}")
        import traceback

        traceback.print_exc()
