#!/usr/bin/env python3
"""
Comprehensive test for all Pydantic schemas.
Tests integration between users, groups, requests, and tags.
"""

from datetime import datetime, timedelta

# Import all schemas through the initialization module (resolves forward references)
from api_v2.schemas import (
    AccessRequestCreate,
    AccessRequestSearch,
    AccessRequestStatus,
    AppGroupRead,
    GroupSearch,
    OktaGroupRead,
    OktaGroupTagMap,
    RoleGroupMap,
    TagRead,
    UserDetail,
    UserSummary,
)


def test_all_schemas_integration():
    """Test comprehensive integration between users, groups, requests, and tags."""
    # 1. Test schema field definitions (without instantiating complex relationships)
    user_summary = UserSummary(
        id="user123",
        email="wumpus@discord.com",
        first_name="Wumpus",
        last_name="Discord",
        display_name="Wumpus Discord",
    )
    assert user_summary.email == "wumpus@discord.com"

    # 2. Test that group schemas have all expected fields
    # Check UserDetail fields
    user_detail_fields = set(UserDetail.model_fields.keys())
    expected_user_fields = {
        "id",
        "created_at",
        "updated_at",
        "email",
        "first_name",
        "last_name",
        "display_name",
        "profile",
        "manager",
        "active_group_memberships",
        "active_group_ownerships",
    }
    missing_user_fields = expected_user_fields - user_detail_fields
    assert not missing_user_fields, f"UserDetail missing fields: {missing_user_fields}"

    # Check OktaGroupRead fields
    okta_group_fields = set(OktaGroupRead.model_fields.keys())
    expected_group_fields = {
        "id",
        "created_at",
        "updated_at",
        "type",
        "name",
        "description",
        "is_managed",
        "externally_managed_data",
        "plugin_data",
        "active_user_memberships_and_ownerships",
        "active_user_memberships",
        "active_user_ownerships",
        "active_role_mappings",
        "active_group_tags",
    }
    missing_group_fields = expected_group_fields - okta_group_fields
    assert not missing_group_fields, f"OktaGroupRead missing fields: {missing_group_fields}"

    # Check AppGroupRead fields
    app_group_fields = set(AppGroupRead.model_fields.keys())
    expected_app_fields = expected_group_fields | {"app_id", "is_owner", "app"}
    missing_app_fields = expected_app_fields - app_group_fields
    assert not missing_app_fields, f"AppGroupRead missing fields: {missing_app_fields}"

    # 3. Test schemas that don't have complex forward references
    # Test AccessRequestCreate
    future_date = datetime.now() + timedelta(days=30)
    access_request_data = {
        "group_id": "12345678901234567890",
        "group_owner": False,
        "reason": "Need access for new project",
        "ending_at": future_date,
    }
    access_request = AccessRequestCreate(**access_request_data)
    assert access_request.group_id == "12345678901234567890"

    # Test TagRead
    tag_data = {
        "id": "tag123",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "name": "Production",
        "description": "Production environment tag",
        "enabled": True,
        "constraints": {"require_manager_approval": True, "max_duration_days": 90},
    }
    tag = TagRead(**tag_data)
    assert tag.name == "Production"
    assert len(tag.constraints) == 2

    # Test search schemas
    group_search = GroupSearch(q="engineering", type="okta_group", page=1, per_page=10)
    assert group_search.q == "engineering"
    assert group_search.type == "okta_group"

    request_search = AccessRequestSearch(
        status=AccessRequestStatus.PENDING, requester_user_id="user123", page=1, per_page=25
    )
    assert request_search.status == AccessRequestStatus.PENDING

    # 4. Test JSON serialization for instantiated schemas
    schemas_to_test = [
        ("UserSummary", user_summary),
        ("AccessRequestCreate", access_request),
        ("TagRead", tag),
        ("GroupSearch", group_search),
        ("AccessRequestSearch", request_search),
    ]

    for name, schema_obj in schemas_to_test:
        json_data = schema_obj.model_dump()
        assert len(json_data) > 0, f"{name} should produce JSON fields"

    # 5. Test enum values
    statuses = [status.value for status in AccessRequestStatus]
    assert len(statuses) > 0, "AccessRequestStatus should have values"

    # 6. Test that schema definitions include relationship fields
    # Test RoleGroupMap has expected fields
    role_map_fields = set(RoleGroupMap.model_fields.keys())
    expected_role_map = {
        "id",
        "created_at",
        "updated_at",
        "role_group_id",
        "group_id",
        "is_owner",
        "ended_at",
        "created_actor_id",
        "ended_actor_id",
        "role_group",
        "group",
        "created_actor",
        "ended_actor",
        "all_group_memberships_and_ownerships",
    }
    missing_role_map = expected_role_map - role_map_fields
    assert not missing_role_map, f"RoleGroupMap missing fields: {missing_role_map}"

    # Test OktaGroupTagMap has expected fields
    tag_map_fields = set(OktaGroupTagMap.model_fields.keys())
    expected_tag_map = {
        "id",
        "created_at",
        "updated_at",
        "group_id",
        "tag_id",
        "ended_at",
        "app_tag_map_id",
        "group",
        "tag",
        "app_tag_mapping",
    }
    missing_tag_map = expected_tag_map - tag_map_fields
    assert not missing_tag_map, f"OktaGroupTagMap missing fields: {missing_tag_map}"

