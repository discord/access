"""
Pydantic schemas for Group-related models.
Converts from Marshmallow group schemas to Pydantic models with discriminated unions.

This handles the polymorphic OktaGroup, RoleGroup, and AppGroup models.
"""
from datetime import datetime
from typing import Union, Literal, Any
from pydantic import Field, Discriminator

from api_v2.schemas.base import BaseSchema, BaseReadSchema, BaseCreateSchema, BaseUpdateSchema
from api_v2.schemas.users import UserSummary


# Type aliases for JSON fields
ExternallyManagedData = dict[str, Any]
PluginData = dict[str, Any]


class GroupBase(BaseSchema):
    """
    Base group schema with common fields for all group types.
    Maps to OktaGroup model fields.
    """
    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    description: str = Field("", max_length=1024, description="Group description")
    is_managed: bool = Field(True, description="Whether this group is managed by Access")
    externally_managed_data: ExternallyManagedData = Field(
        default_factory=dict,
        description="Additional data for externally managed groups"
    )
    plugin_data: PluginData = Field(
        default_factory=dict,
        description="Data for group plugin integrations"
    )


class GroupSummary(BaseSchema):
    """
    Minimal group information for use in relationships.
    Avoids circular references and reduces payload size.
    """
    id: str = Field(..., description="Group ID")
    type: str = Field(..., description="Group type (okta_group, role_group, app_group)")
    name: str = Field(..., description="Group name")
    description: str = Field("", description="Group description")
    deleted_at: datetime | None = Field(None, description="When group was deleted")


# Discriminated Union for Polymorphic Groups

class OktaGroupRead(BaseReadSchema, GroupBase):
    """
    Standard Okta group schema.
    This is the base group type.
    """
    type: Literal["okta_group"] = Field("okta_group", description="Group type discriminator")
    
    # Relationships - temporarily simplified until we implement membership schemas
    # active_user_memberships: list[UserGroupMembershipSummary] = Field(
    #     default_factory=list,
    #     description="Current active user memberships"
    # )
    # active_user_ownerships: list[UserGroupMembershipSummary] = Field(
    #     default_factory=list,
    #     description="Current active user ownerships"
    # )


class RoleGroupRead(BaseReadSchema, GroupBase):
    """
    Role group schema.
    Role groups grant permissions to other groups.
    """
    type: Literal["role_group"] = Field("role_group", description="Group type discriminator")
    
    # Role-specific relationships
    # active_role_associated_group_mappings: list[RoleGroupMappingSummary] = Field(
    #     default_factory=list,
    #     description="Groups that this role grants access to"
    # )


class AppGroupRead(BaseReadSchema, GroupBase):
    """
    App group schema.
    App groups are associated with applications.
    """
    type: Literal["app_group"] = Field("app_group", description="Group type discriminator")
    app_id: str = Field(..., description="ID of the associated application")
    is_owner: bool = Field(False, description="Whether this is an app owner group")
    
    # App-specific relationships
    # app: AppSummary = Field(None, description="Associated application")


# Discriminated Union for all group types
def get_group_type(v: Any) -> str:
    """Discriminator function to determine group type."""
    if isinstance(v, dict):
        return v.get("type", "okta_group")
    return getattr(v, "type", "okta_group")


GroupRead = Union[OktaGroupRead, RoleGroupRead, AppGroupRead]
GroupReadAnnotated = Field(discriminator=Discriminator(get_group_type))


# Create/Update schemas for groups

class OktaGroupCreate(BaseCreateSchema, GroupBase):
    """Schema for creating standard Okta groups."""
    type: Literal["okta_group"] = Field("okta_group", description="Group type")
    
    # Tags for creation (load-only)
    tags_to_add: list[str] = Field(
        default_factory=list,
        description="Tag IDs to add to the group"
    )


class RoleGroupCreate(BaseCreateSchema, GroupBase):
    """Schema for creating role groups."""
    type: Literal["role_group"] = Field("role_group", description="Group type")
    
    # Role groups have name prefix requirements
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Role group name (must start with 'Role-')"
    )


class AppGroupCreate(BaseCreateSchema, GroupBase):
    """Schema for creating app groups."""
    type: Literal["app_group"] = Field("app_group", description="Group type")
    app_id: str = Field(..., description="ID of the associated application")
    is_owner: bool = Field(False, description="Whether this is an app owner group")
    
    # App groups have name prefix requirements
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="App group name (must start with 'App-')"
    )


# Union for create operations
GroupCreate = Union[OktaGroupCreate, RoleGroupCreate, AppGroupCreate]
GroupCreateAnnotated = Field(discriminator=Discriminator(get_group_type))


# Update schemas (all fields optional)

class GroupUpdate(BaseUpdateSchema):
    """
    Schema for updating groups.
    All fields are optional to support partial updates.
    """
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1024)
    is_managed: bool | None = None
    externally_managed_data: ExternallyManagedData | None = None
    plugin_data: PluginData | None = None
    
    # Tag operations (for group updates)
    tags_to_add: list[str] = Field(
        default_factory=list,
        description="Tag IDs to add to the group"
    )
    tags_to_remove: list[str] = Field(
        default_factory=list,
        description="Tag IDs to remove from the group"
    )


# Search and pagination schemas

class GroupSearch(BaseSchema):
    """Schema for group search parameters."""
    q: str | None = Field(None, description="Search query across name and description")
    type: str | None = Field(None, description="Filter by group type")
    app_id: str | None = Field(None, description="Filter by application ID (for app groups)")
    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(25, ge=1, le=100, description="Items per page")


class GroupList(BaseReadSchema, GroupBase):
    """
    Simplified schema for group lists.
    Used for search results and basic group listings.
    """
    type: str = Field(..., description="Group type")
    
    # For app groups, include app info
    app_id: str | None = Field(None, description="Application ID (app groups only)")
    is_owner: bool | None = Field(None, description="Is owner group (app groups only)")


# Member management schemas

class GroupMemberAction(BaseSchema):
    """Schema for adding/removing group members."""
    user_ids: list[str] = Field(..., min_items=1, description="User IDs to add/remove")
    is_owner: bool = Field(False, description="Grant ownership permissions")
    reason: str = Field(..., min_length=1, max_length=1024, description="Reason for the change")


class GroupMemberList(BaseSchema):
    """Schema for listing group members."""
    members: list[UserSummary] = Field(default_factory=list, description="Group members")
    owners: list[UserSummary] = Field(default_factory=list, description="Group owners")
    total_members: int = Field(0, description="Total number of members")
    total_owners: int = Field(0, description="Total number of owners")