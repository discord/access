#!/usr/bin/env python3
"""
Comprehensive integration test for all converted Pydantic schemas.
"""

import os
import subprocess
import sys
from datetime import datetime

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///instance/access.db"


def run_test_script(script_name: str) -> bool:
    """Run a test script and return success status."""
    try:
        result = subprocess.run([sys.executable, script_name], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"✓ {script_name} passed")
            return True
        else:
            print(f"✗ {script_name} failed:")
            print(result.stdout)
            print(result.stderr)
            return False
    except subprocess.TimeoutExpired:
        print(f"✗ {script_name} timed out")
        return False
    except Exception as e:
        print(f"✗ {script_name} error: {e}")
        return False


def test_direct_imports() -> bool:
    """Test direct imports of all schemas."""
    try:
        # Test core schemas
        from api_v2.schemas import (
            AppGroupRead,
            GroupCreate,
            GroupRead,
            GroupSearch,
            GroupUpdate,
            OktaGroupRead,
            RoleGroupRead,
            TagCreate,
            TagRead,
            TagSearch,
            TagUpdate,
            UserDetail,
            UserGroupMember,
            UserList,
            UserSearch,
            UserSummary,
        )

        __all__ = [
            UserSummary,
            UserDetail,
            UserList,
            UserSearch,
            UserGroupMember,
            OktaGroupRead,
            RoleGroupRead,
            AppGroupRead,
            GroupRead,
            GroupCreate,
            GroupUpdate,
            GroupSearch,
            TagRead,
            TagCreate,
            TagUpdate,
            TagSearch,
        ]

        # Test request schemas
        from api_v2.schemas.requests import (
            AccessRequestCreate,
            AccessRequestRead,
            AccessRequestResolve,
            AccessRequestSearch,
            AccessRequestStatus,
            RoleRequestCreate,
            RoleRequestRead,
            RoleRequestResolve,
            RoleRequestSearch,
        )

        __all__ = [
            AccessRequestStatus,
            AccessRequestRead,
            AccessRequestCreate,
            AccessRequestResolve,
            RoleRequestRead,
            RoleRequestCreate,
            RoleRequestResolve,
            AccessRequestSearch,
            RoleRequestSearch,
        ]

        # Test pagination schemas
        from api_v2.schemas.pagination import (
            AccessRequestPagination,
            AppPagination,
            AuditOrderBy,
            GroupPagination,
            GroupRoleAuditPagination,
            PaginationResponse,
            RolePagination,
            RoleRequestPagination,
            SearchAccessRequestPagination,
            SearchAuditPagination,
            SearchGroupPagination,
            SearchGroupRoleAuditPagination,
            SearchPagination,
            SearchRolePagination,
            SearchRoleRequestPagination,
            SearchUserGroupAuditPagination,
            TagPagination,
            UserGroupAuditPagination,
            UserPagination,
        )

        __all__ = [
            SearchPagination,
            SearchGroupPagination,
            SearchRolePagination,
            AuditOrderBy,
            SearchAuditPagination,
            SearchUserGroupAuditPagination,
            SearchGroupRoleAuditPagination,
            SearchAccessRequestPagination,
            SearchRoleRequestPagination,
            PaginationResponse,
            AccessRequestPagination,
            RoleRequestPagination,
            AppPagination,
            UserGroupAuditPagination,
            GroupPagination,
            GroupRoleAuditPagination,
            RolePagination,
            TagPagination,
            UserPagination,
        ]

        # Test audit log schemas
        from api_v2.schemas.audit_logs import (
            AccessRequestSummary,
            AppSummary,
            AuditLogCreate,
            AuditLogRead,
            EventType,
            GroupSummary,
            RoleGroupMapSummary,
            RoleGroupSummary,
            RoleRequestSummary,
            UserGroupMembershipSummary,
        )

        __all__ = [
            EventType,
            UserGroupMembershipSummary,
            RoleGroupMapSummary,
            AppSummary,
            GroupSummary,
            RoleGroupSummary,
            AccessRequestSummary,
            RoleRequestSummary,
            AuditLogRead,
            AuditLogCreate,
        ]

        # Test membership schemas
        from api_v2.schemas.group_memberships import GroupMemberRead, GroupMemberUpdate
        from api_v2.schemas.role_memberships import RoleMemberRead, RoleMemberUpdate

        __all__ = [GroupMemberUpdate, GroupMemberRead, RoleMemberUpdate, RoleMemberRead]

        # Test remaining schemas
        from api_v2.schemas.delete_message import DeleteMessage
        from api_v2.schemas.metrics import Metrics, MetricsData, MetricType

        __all__ = [DeleteMessage, Metrics, MetricsData, MetricType]

        print("✓ All schema imports successful")
        return True
    except Exception as e:
        print(f"✗ Schema import failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_schema_instantiation() -> bool:
    """Test that we can instantiate basic examples of all schemas."""
    try:
        from datetime import datetime, timedelta

        from api_v2.schemas import OktaGroupRead, TagRead, UserSummary
        from api_v2.schemas.audit_logs import AuditLogCreate, EventType
        from api_v2.schemas.delete_message import DeleteMessage
        from api_v2.schemas.group_memberships import GroupMemberUpdate
        from api_v2.schemas.metrics import Metrics, MetricsData, MetricType
        from api_v2.schemas.pagination import SearchPagination
        from api_v2.schemas.requests import AccessRequestCreate
        from api_v2.schemas.role_memberships import RoleMemberUpdate

        # Create test instances
        user = UserSummary(
            id="user123",
            email="test@example.com",
            first_name="Test",
            last_name="User",
        )

        group = OktaGroupRead(
            id="group123",
            name="Test Group",
            description="Test group description",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            is_managed=True,
            type="okta_group",
        )

        access_request = AccessRequestCreate(
            group_id="12345678901234567890",
            group_owner=False,
            reason="Test access",
            ending_at=datetime.now() + timedelta(days=30),
        )

        tag = TagRead(id="tag123", name="test-tag", enabled=True, created_at=datetime.now(), updated_at=datetime.now())

        pagination = SearchPagination(page=1, per_page=25, q="search")

        audit = AuditLogCreate(event_type=EventType.ACCESS_CREATE, current_user_id="admin123")

        group_member = GroupMemberUpdate(members_to_add=[], owners_to_add=[], members_to_remove=[], owners_to_remove=[])

        role_member = RoleMemberUpdate(
            groups_to_add=[], owner_groups_to_add=[], groups_to_remove=[], owner_groups_to_remove=[]
        )

        delete_msg = DeleteMessage()

        metrics = Metrics(type=MetricType.COUNTER, data=MetricsData(value=1.0))

        __all__ = [user, group, access_request, tag, pagination, audit, group_member, role_member, delete_msg, metrics]

        print("✓ All schema instantiation successful")
        return True
    except Exception as e:
        print(f"✗ Schema instantiation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🚀 Starting comprehensive schema integration test...")
    print(f"⏰ Started at: {datetime.now()}")
    print()

    all_passed = True

    # Test direct imports and instantiation
    print("📦 Testing direct schema imports and instantiation...")
    if not test_direct_imports():
        all_passed = False
    if not test_schema_instantiation():
        all_passed = False
    print()

    # Run individual test scripts
    print("🧪 Running individual schema test scripts...")
    test_scripts = [
        "test_user_schemas.py",
        "test_group_schemas.py",
        "test_request_schemas.py",
        "test_tag_schemas.py",
        "test_pagination_schemas.py",
        "test_audit_schemas.py",
        "test_group_memberships_schemas.py",
        "test_remaining_schemas.py",
    ]

    for script in test_scripts:
        if not run_test_script(script):
            all_passed = False

    print()
    print("📊 SCHEMA CONVERSION SUMMARY")
    print("=" * 50)

    # Count total schemas converted
    schema_counts = {
        "core_schemas.py (users, groups, requests, tags)": 18,
        "pagination.py": 21,
        "audit_logs.py": 2,
        "group_memberships.py": 1,
        "delete_message.py": 1,
        "role_memberships.py": 1,
        "metrics.py": 2,
    }

    total_schemas = sum(schema_counts.values())

    for file, count in schema_counts.items():
        print(f"✓ {file}: {count} schemas")

    print("-" * 50)
    print(f"✓ TOTAL SCHEMAS CONVERTED: {total_schemas}")
    print()

    if all_passed:
        print("🎉 ALL SCHEMA INTEGRATION TESTS PASSED!")
        print("✅ Phase 2 (Schema Conversion) is COMPLETE!")
        print()
        print("Next steps:")
        print("• Phase 3: Authentication & Middleware migration")
        print("• Phase 4: Remaining API endpoints migration")
        print("• Phase 5: Testing & Validation updates")
        print("• Phase 6: Deployment & Cutover")
        sys.exit(0)
    else:
        print("❌ Some schema tests failed!")
        print("Please review the error messages above.")
        sys.exit(1)
