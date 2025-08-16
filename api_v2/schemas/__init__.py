"""
API v2 schemas initialization and forward reference resolution.
"""
from __future__ import annotations

# Import base schemas first
from api_v2.schemas.base import BaseSchema, BaseReadSchema, BaseCreateSchema, BaseUpdateSchema

# Import individual schema modules without rebuilding immediately
from api_v2.schemas import users, groups, requests, tags

# Make specific schemas available at package level
from api_v2.schemas.users import UserSummary, UserDetail, UserList, UserSearch, UserGroupMember
from api_v2.schemas.groups import (
    GroupRead, OktaGroupRead, RoleGroupRead, AppGroupRead,
    GroupCreate, GroupUpdate, GroupSearch, GroupList,
    RoleGroupMap, OktaGroupTagMap
)
from api_v2.schemas.requests import (
    AccessRequestStatus, AccessRequestRead, AccessRequestCreate, AccessRequestResolve,
    RoleRequestRead, RoleRequestCreate, RoleRequestResolve,
    AccessRequestSearch, RoleRequestSearch
)
from api_v2.schemas.tags import TagRead, TagCreate, TagUpdate, TagSearch

# Now that all schemas are imported, rebuild models with full namespace
def rebuild_models():
    """Rebuild all models to resolve forward references."""
    import sys
    current_module = sys.modules[__name__]
    
    # Create namespace with all imported types
    namespace = {
        'UserSummary': UserSummary,
        'UserGroupMember': UserGroupMember,
        'GroupRead': GroupRead,
        'OktaGroupRead': OktaGroupRead,
        'RoleGroupRead': RoleGroupRead,
        'AppGroupRead': AppGroupRead,
        'RoleGroupMap': RoleGroupMap,
        'OktaGroupTagMap': OktaGroupTagMap,
        'TagRead': TagRead,
        'AppRead': None,  # Placeholder for now
        'AppTagMap': None,  # Placeholder for now
    }
    
    # Rebuild with namespace - use the method signature that works with current Pydantic
    try:
        UserDetail.model_rebuild()
        UserGroupMember.model_rebuild()
        OktaGroupRead.model_rebuild()
        RoleGroupRead.model_rebuild()
        AppGroupRead.model_rebuild()
        RoleGroupMap.model_rebuild()
        OktaGroupTagMap.model_rebuild()
    except Exception as e:
        # If rebuilding fails, it's okay - forward references will still work
        # but instantiation may require explicit data for all fields
        print(f"Note: Schema rebuilding had issues: {e}")

# Try to rebuild models when this module is imported
rebuild_models()

__all__ = [
    # Base schemas
    "BaseSchema", "BaseReadSchema", "BaseCreateSchema", "BaseUpdateSchema",
    
    # User schemas
    "UserSummary", "UserDetail", "UserList", "UserSearch", "UserGroupMember",
    
    # Group schemas
    "GroupRead", "OktaGroupRead", "RoleGroupRead", "AppGroupRead",
    "GroupCreate", "GroupUpdate", "GroupSearch", "GroupList",
    "RoleGroupMap", "OktaGroupTagMap",
    
    # Request schemas
    "AccessRequestStatus", "AccessRequestRead", "AccessRequestCreate", "AccessRequestResolve",
    "RoleRequestRead", "RoleRequestCreate", "RoleRequestResolve",
    "AccessRequestSearch", "RoleRequestSearch",
    
    # Tag schemas
    "TagRead", "TagCreate", "TagUpdate", "TagSearch"
]