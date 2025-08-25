"""
Test script for authentication dependencies.
Tests that the auth dependencies can import and validate correctly.
"""

from api_v2.auth.dependencies import CloudflareAuth
from api_v2.auth.authorization import (
    AuthorizationHelpers,
    require_app_management_permission,
    require_group_management_permission,
)


def test_auth_dependencies():
    """Test authentication dependencies can import and validate correctly."""
    # Test 3: Test CloudflareAuth helper methods exist
    assert hasattr(CloudflareAuth, "get_public_keys")
    assert hasattr(CloudflareAuth, "verify_cloudflare_token")

    # Test 4: Test AuthorizationHelpers methods exist
    helper_methods = ["is_group_owner", "is_app_owner_group_owner", "is_access_admin", "can_manage_group"]

    for method in helper_methods:
        assert hasattr(AuthorizationHelpers, method)

    # Test 5: Test that dependency factory functions work
    group_permission_dep = require_group_management_permission("test-group-id")
    app_permission_dep = require_app_management_permission("test-app-id")

    assert callable(group_permission_dep)
    assert callable(app_permission_dep)
