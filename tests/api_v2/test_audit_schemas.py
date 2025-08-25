#!/usr/bin/env python3
"""
Test script for audit log Pydantic schemas.
"""

from datetime import datetime, timedelta

# Test audit log schemas
from api_v2.schemas import (
    AuditAccessRequestSummary,
    AuditAppSummary,
    AuditEventType,
    AuditGroupSummary,
    AuditLogCreate,
    AuditLogRead,
    AuditRoleGroupMapSummary,
    AuditRoleGroupSummary,
    AuditRoleRequestSummary,
    AuditUserGroupMembershipSummary,
    AuditUserSummary,
)


def test_audit_schemas():
    """Test audit log Pydantic schemas."""
    # Audit log schemas imported successfully

    # Test AuditEventType enum
    assert len(AuditEventType) > 0, "AuditEventType enum should have event types"
    # Sample events: ACCESS_CREATE.value, GROUP_MODIFY_USER.value

    # Test summary schemas
    user_summary = AuditUserSummary(
        id="user123", email="wumpus@discord.com", first_name="Test", last_name="User", display_name="Test User"
    )
    assert user_summary.email == "wumpus@discord.com"

    membership_summary = AuditUserGroupMembershipSummary(user_id="user123", group_id="group456")
    assert membership_summary.user_id == "user123"

    role_map_summary = AuditRoleGroupMapSummary(role_group_id="role123", group_id="group456")
    assert role_map_summary.role_group_id == "role123"

    app_summary = AuditAppSummary(id="app123", name="Test Application")
    assert app_summary.name == "Test Application"

    group_summary = AuditGroupSummary(id="group123", name="Test Group", type="okta_group", app=app_summary)
    assert group_summary.name == "Test Group"
    assert group_summary.type == "okta_group"

    role_summary = AuditRoleGroupSummary(id="role123", name="Admin Role")
    assert role_summary.name == "Admin Role"

    # Test request summaries
    access_request_summary = AuditAccessRequestSummary(
        id="req123",
        request_reason="Need access for project",
        request_ending_at=datetime.now() + timedelta(days=30),
        request_ownership=True,
    )
    assert access_request_summary.id == "req123"

    role_request_summary = AuditRoleRequestSummary(
        id="rolereq123",
        requester_role=role_summary,
        request_reason="Role access needed",
        request_ending_at=datetime.now() + timedelta(days=60),
        request_ownership=False,
    )
    assert role_request_summary.id == "rolereq123"

    # Test AuditLogCreate (simpler schema)
    audit_create = AuditLogCreate(
        event_type=AuditEventType.GROUP_MODIFY_USER,
        user_agent="Mozilla/5.0",
        ip="192.168.1.100",
        current_user_id="admin123",
        current_user_email="admin@company.com",
        additional_data={"group_id": "group123", "action": "add_member"},
    )
    assert audit_create.event_type == AuditEventType.GROUP_MODIFY_USER

    # Test comprehensive AuditLogRead
    audit_log = AuditLogRead(
        id="audit123",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        event_type=AuditEventType.GROUP_MODIFY_USER,
        user_agent="Mozilla/5.0",
        ip="192.168.1.100",
        current_user_id="admin123",
        current_user_email="admin@company.com",
        group=group_summary,
        old_group_name="Old Group Name",
        group_owners=[user_summary],
        members_added_ids_emails=[user_summary],
        owners_should_expire_user_id_group_id=[membership_summary],
        role=role_summary,
        groups_added_ending_at=datetime.now() + timedelta(days=90),
        owner_groups_added_ids_names=[role_summary],
        groups_should_expire_role_id_group_id=[role_map_summary],
        request=access_request_summary,
        role_request=role_request_summary,
        requester=user_summary,
        app=app_summary,
        old_app_name="Old App Name",
    )
    assert audit_log.event_type == AuditEventType.GROUP_MODIFY_USER
    assert audit_log.group.name == "Test Group"

    # Test JSON serialization
    audit_json = audit_log.model_dump()
    assert len(audit_json) > 0, "JSON serialization should work"

    # Test with minimal data (most fields optional)
    minimal_audit = AuditLogRead(
        id="minimal123", created_at=datetime.now(), updated_at=datetime.now(), event_type=AuditEventType.TAG_CREATE
    )
    assert minimal_audit.event_type == AuditEventType.TAG_CREATE

    # Test field validation - enum values
    valid_events = [e.value for e in AuditEventType]
    assert len(valid_events) > 0, "All event types should be valid enum values"

    # Audit log schemas test completed successfully:
    # - AuditEventType enum with multiple event types  
    # - All summary schemas working (AuditUserGroupMembershipSummary, AuditRoleGroupMapSummary, etc.)
    # - AuditLogRead schema with comprehensive fields
    # - AuditLogCreate schema for internal use
    # - Optional field handling working
    # - Nested schema relationships working
    # - JSON serialization working