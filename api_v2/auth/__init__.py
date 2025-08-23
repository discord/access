"""
Authentication module for FastAPI.
Provides dependencies and utilities for authentication and authorization.
"""

from .authorization import (
    AuthorizationHelpers,
    require_access_admin,
    require_access_admin_or_app_creator,
    require_app_management_permission,
    require_group_management_permission,
)
from .dependencies import CloudflareAuth, get_current_user, get_current_user_optional

__all__ = [
    # Authentication dependencies
    "get_current_user",
    "get_current_user_optional", 
    "CloudflareAuth",
    # Authorization dependencies
    "AuthorizationHelpers",
    "require_access_admin",
    "require_access_admin_or_app_creator",
    "require_group_management_permission",
    "require_app_management_permission",
]