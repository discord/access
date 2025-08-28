"""
Authentication module for FastAPI.
Provides dependencies and utilities for authentication and authorization.
"""

from .authentication import get_cloudflare_auth, get_oidc_auth
from .authorization import (
    AuthorizationHelpers,
    require_access_admin,
    require_access_admin_or_app_creator,
    require_app_management_permission,
    require_group_management_permission,
)
from .dependencies import get_current_user, get_current_user_optional
from .middleware import AuthenticationMiddleware, public_route

__all__ = [
    # Authentication dependencies
    "get_current_user",
    "get_current_user_optional",
    # Authorization dependencies
    "AuthorizationHelpers",
    "require_access_admin",
    "require_access_admin_or_app_creator",
    "require_group_management_permission",
    "require_app_management_permission",
    # Authentication
    "get_cloudflare_auth",
    "get_oidc_auth",
    # Middleware
    "AuthenticationMiddleware",
    "public_route",
]
