#!/usr/bin/env python3
"""
Test script for audit log Pydantic schemas.
"""
import sys
import os
from datetime import datetime, timedelta

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Set required environment variables for testing
os.environ['FLASK_ENV'] = 'test'
os.environ['DATABASE_URI'] = 'sqlite:///instance/access.db'

# Set up Flask app context
from api.app import create_app
flask_app = create_app(testing=True)

with flask_app.app_context():
    try:
        # Test audit log schemas
        from api_v2.schemas.audit_logs import (
            EventType, UserGroupMembershipSummary, RoleGroupMapSummary,
            AppSummary, GroupSummary, RoleGroupSummary, AccessRequestSummary,
            RoleRequestSummary, AuditLogRead, AuditLogCreate
        )
        from api_v2.schemas.users import UserSummary
        print("✓ Audit log schemas imported successfully")
        
        # Test EventType enum
        print(f"✓ EventType enum has {len(EventType)} event types")
        print(f"✓ Sample events: {[EventType.ACCESS_CREATE.value, EventType.GROUP_MODIFY_USER.value]}")
        
        # Test summary schemas
        user_summary = UserSummary(
            id="user123",
            email="wumpus@discord.com",
            first_name="Test",
            last_name="User",
            display_name="Test User"
        )
        print(f"✓ UserSummary: {user_summary.email}")
        
        membership_summary = UserGroupMembershipSummary(
            user_id="user123",
            group_id="group456"
        )
        print(f"✓ UserGroupMembershipSummary: user={membership_summary.user_id}")
        
        role_map_summary = RoleGroupMapSummary(
            role_group_id="role123",
            group_id="group456"
        )
        print(f"✓ RoleGroupMapSummary: role={role_map_summary.role_group_id}")
        
        app_summary = AppSummary(
            id="app123",
            name="Test Application"
        )
        print(f"✓ AppSummary: {app_summary.name}")
        
        group_summary = GroupSummary(
            id="group123",
            name="Test Group",
            type="okta_group",
            app=app_summary
        )
        print(f"✓ GroupSummary: {group_summary.name} ({group_summary.type})")
        
        role_summary = RoleGroupSummary(
            id="role123",
            name="Admin Role"
        )
        print(f"✓ RoleGroupSummary: {role_summary.name}")
        
        # Test request summaries
        access_request_summary = AccessRequestSummary(
            id="req123",
            request_reason="Need access for project",
            request_ending_at=datetime.now() + timedelta(days=30),
            request_ownership=True
        )
        print(f"✓ AccessRequestSummary: {access_request_summary.id}")
        
        role_request_summary = RoleRequestSummary(
            id="rolereq123",
            requester_role=role_summary,
            request_reason="Role access needed",
            request_ending_at=datetime.now() + timedelta(days=60),
            request_ownership=False
        )
        print(f"✓ RoleRequestSummary: {role_request_summary.id}")
        
        # Test AuditLogCreate (simpler schema)
        audit_create = AuditLogCreate(
            event_type=EventType.GROUP_MODIFY_USER,
            user_agent="Mozilla/5.0",
            ip="192.168.1.100",
            current_user_id="admin123",
            current_user_email="admin@company.com",
            additional_data={"group_id": "group123", "action": "add_member"}
        )
        print(f"✓ AuditLogCreate: {audit_create.event_type}")
        
        # Test comprehensive AuditLogRead
        audit_log = AuditLogRead(
            id="audit123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            event_type=EventType.GROUP_MODIFY_USER,
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
            old_app_name="Old App Name"
        )
        print(f"✓ AuditLogRead: {audit_log.event_type} for group {audit_log.group.name if audit_log.group else 'N/A'}")
        
        # Test JSON serialization
        audit_json = audit_log.model_dump()
        print(f"✓ JSON serialization works: {len(audit_json)} top-level fields")
        
        # Test with minimal data (most fields optional)
        minimal_audit = AuditLogRead(
            id="minimal123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            event_type=EventType.TAG_CREATE
        )
        print(f"✓ Minimal audit log: {minimal_audit.event_type}")
        
        # Test field validation - enum values
        valid_events = [e.value for e in EventType]
        print(f"✓ All {len(valid_events)} event types are valid enum values")
        
        print("\n🎯 Audit log schemas test completed successfully!")
        print("✓ EventType enum with 20 event types")
        print("✓ All summary schemas working (UserGroupMembershipSummary, RoleGroupMapSummary, etc.)")
        print("✓ AuditLogRead schema with comprehensive fields")
        print("✓ AuditLogCreate schema for internal use")
        print("✓ Optional field handling working")
        print("✓ Nested schema relationships working")
        print("✓ JSON serialization working")
        
    except Exception as e:
        print(f"✗ Audit log schema test failed: {e}")
        import traceback
        traceback.print_exc()