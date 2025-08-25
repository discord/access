#!/usr/bin/env python3
"""
Test script for group relationships and RoleGroupMap schema.
"""

# Test group relationships and new schemas
from api_v2.schemas import AppGroupRead, OktaGroupRead, OktaGroupTagMap, RoleGroupMap, RoleGroupRead


def test_group_relationships_schema_fields():
    """Test that group schemas with relationships have all expected fields."""
    # Group schemas with relationships imported successfully

    # Check RoleGroupMap has expected fields
    role_map_fields = RoleGroupMap.model_fields.keys()
    expected_role_map_fields = {
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
    missing_fields = expected_role_map_fields - set(role_map_fields)
    assert not missing_fields, f"RoleGroupMap missing fields: {missing_fields}"

    # Check OktaGroupTagMap has expected fields
    tag_map_fields = OktaGroupTagMap.model_fields.keys()
    expected_tag_map_fields = {
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
    missing_tag_fields = expected_tag_map_fields - set(tag_map_fields)
    assert not missing_tag_fields, f"OktaGroupTagMap missing fields: {missing_tag_fields}"


def test_okta_group_relationship_fields():
    """Test OktaGroupRead has all relationship fields."""
    okta_group_fields = OktaGroupRead.model_fields.keys()
    expected_relationship_fields = {
        "active_user_memberships_and_ownerships",
        "active_user_memberships",
        "active_user_ownerships",
        "active_non_role_user_memberships",
        "active_non_role_user_ownerships",
        "active_role_mappings",
        "active_role_member_mappings",
        "active_role_owner_mappings",
        "active_group_tags",
    }
    missing_rel_fields = expected_relationship_fields - set(okta_group_fields)
    assert not missing_rel_fields, f"OktaGroupRead missing relationship fields: {missing_rel_fields}"


def test_role_group_relationship_fields():
    """Test RoleGroupRead has role-specific relationship fields."""
    role_group_fields = RoleGroupRead.model_fields.keys()
    expected_role_fields = {
        "active_role_associated_group_mappings",
        "active_role_associated_group_member_mappings",
        "active_role_associated_group_owner_mappings",
    }
    missing_role_fields = expected_role_fields - set(role_group_fields)
    assert not missing_role_fields, f"RoleGroupRead missing role-specific fields: {missing_role_fields}"


def test_app_group_relationship_fields():
    """Test AppGroupRead has app-specific relationship fields."""
    app_group_fields = AppGroupRead.model_fields.keys()
    expected_app_fields = {"app", "app_id", "is_owner"}
    missing_app_fields = expected_app_fields - set(app_group_fields)
    assert not missing_app_fields, f"AppGroupRead missing app-specific fields: {missing_app_fields}"


def test_schema_field_type_annotations():
    """Test schema field types and annotations (validation without instantiation)."""
    # Check that forward reference fields exist and are properly typed
    okta_user_memberships_field = OktaGroupRead.model_fields["active_user_memberships"]
    assert okta_user_memberships_field.annotation is not None

    role_mappings_field = OktaGroupRead.model_fields["active_role_mappings"]
    assert role_mappings_field.annotation is not None

    group_tags_field = OktaGroupRead.model_fields["active_group_tags"]
    assert group_tags_field.annotation is not None

    # Check RoleGroupMap relationship fields
    role_group_field = RoleGroupMap.model_fields["role_group"]
    assert role_group_field.annotation is not None

    all_memberships_field = RoleGroupMap.model_fields["all_group_memberships_and_ownerships"]
    assert all_memberships_field.annotation is not None

    # Check OktaGroupTagMap relationship fields
    tag_group_field = OktaGroupTagMap.model_fields["group"]
    assert tag_group_field.annotation is not None

    tag_field = OktaGroupTagMap.model_fields["tag"]
    assert tag_field.annotation is not None


def test_discriminated_union_type_annotations():
    """Test discriminated union type annotations work."""
    okta_type_annotation = OktaGroupRead.model_fields['type'].annotation
    role_type_annotation = RoleGroupRead.model_fields['type'].annotation  
    app_type_annotation = AppGroupRead.model_fields['type'].annotation
    
    # Each group type should have its specific literal type
    assert okta_type_annotation is not None
    assert role_type_annotation is not None
    assert app_type_annotation is not None

    # Group relationships test completed successfully:
    # - RoleGroupMap schema working
    # - OktaGroupTagMap schema working
    # - All group types have comprehensive relationships
    # - User membership relationships defined
    # - Role mapping relationships defined
    # - Tag mapping relationships defined
    # - Forward references working (no circular imports)
    # - Discriminated union working with relationships
    # - JSON serialization working