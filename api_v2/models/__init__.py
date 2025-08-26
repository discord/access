"""
SQLAlchemy models for FastAPI application.
Pure SQLAlchemy models without Flask dependencies.
"""

from .base import Base
from .core_models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    AppTagMap,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
    Tag,
)

# Helper functions
from .access_request import get_all_possible_request_approvers
from .app_group import get_access_owners, get_app_managers, app_owners_group_description
from .okta_group import get_group_managers
from .tag import coalesce_constraints, coalesce_ended_at

__all__ = [
    "Base",
    "AccessRequest",
    "AccessRequestStatus",
    "App",
    "AppGroup",
    "AppTagMap",
    "OktaGroup",
    "OktaGroupTagMap",
    "OktaUser",
    "OktaUserGroupMember",
    "RoleGroup",
    "RoleGroupMap",
    "RoleRequest",
    "Tag",
    # Helper functions
    "get_all_possible_request_approvers",
    "get_access_owners",
    "get_app_managers",
    "app_owners_group_description",
    "get_group_managers",
    "coalesce_constraints",
    "coalesce_ended_at",
]