"""
Pydantic schemas for User-related models.
Converts from Marshmallow OktaUserSchema to Pydantic models.

Note: Users are managed by Okta and synchronized, not created/updated via API.
Only read operations are supported.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Discriminator, EmailStr, Field

from api_v2.schemas.base import BaseCreateSchema, BaseReadSchema, BaseSchema, BaseUpdateSchema, ProfileDict


class UserSummary(BaseSchema):
    """
    Minimal user information for use in relationships.
    Avoids circular references and reduces payload size.
    """

    id: str = Field(..., description="User ID")
    email: EmailStr = Field(..., description="User's email address")
    first_name: str = Field(..., description="User's first name")
    last_name: str = Field(..., description="User's last name")
    display_name: str | None = Field(None, description="User's display name")
    deleted_at: datetime | None = Field(None, description="When user was deleted")


class UserBase(BaseSchema):
    """
    Base user model with core fields.
    Maps to OktaUser model fields.
    """

    email: EmailStr = Field(..., description="User's email address")
    first_name: str = Field(..., max_length=255, description="User's first name")
    last_name: str = Field(..., max_length=255, description="User's last name")
    display_name: str | None = Field(None, max_length=255, description="User's display name")
    profile: ProfileDict = Field(default_factory=dict, description="Custom Okta profile attributes")


class UserDetail(BaseReadSchema, UserBase):
    """
    Schema for detailed user data with relationships.
    Used by GET /users/{user_id} endpoint.
    """

    # Manager relationship (avoid circular reference with Optional)
    manager: UserSummary | None = Field(None, description="User's manager")

    # Membership relationships - populated by the API layer
    all_group_memberships_and_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="All user memberships and ownerships"
    )
    active_group_memberships_and_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active group memberships and ownerships"
    )
    active_group_memberships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active group memberships (not ownerships)"
    )
    active_group_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active group ownerships"
    )


class UserList(BaseReadSchema, UserBase):
    """
    Schema for user list/search results.
    Used by GET /users endpoint - includes minimal fields for performance.
    """

    # Only includes basic fields, no relationships for list view
    pass


class UserSearch(BaseSchema):
    """
    Schema for user search parameters.
    """

    q: str | None = Field(None, description="Search query across name, email, and custom attributes")
    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(25, ge=1, le=100, description="Items per page")


class UserGroupMember(BaseReadSchema):
    """
    Schema for user-group membership/ownership relationships.
    Maps to OktaUserGroupMember model.
    """

    user_id: str = Field(..., description="User ID")
    group_id: str = Field(..., description="Group ID")
    role_group_map_id: int | None = Field(None, description="Role group mapping ID if membership via role")
    is_owner: bool = Field(False, description="Is this user an owner of the group")
    created_reason: str = Field(default="", description="Reason for adding user to group")
    should_expire: bool = Field(False, description="Should this membership expire")
    ended_at: datetime | None = Field(None, description="When membership ended")
    created_actor_id: str | None = Field(None, description="ID of user who created this membership")
    ended_actor_id: str | None = Field(None, description="ID of user who ended this membership")

    # Forward references to avoid circular imports - will be resolved at runtime
    group: "GroupRead | None" = Field(None, description="Group this membership is for")
    user: UserSummary | None = Field(None, description="User this membership is for")
    created_actor: UserSummary | None = Field(None, description="User who created this membership")
    ended_actor: UserSummary | None = Field(None, description="User who ended this membership")


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
        default_factory=dict, description="Additional data for externally managed groups"
    )
    plugin_data: PluginData = Field(default_factory=dict, description="Data for group plugin integrations")


class NonRoleGroupReadBase(BaseReadSchema, GroupBase):
    """
    Base group schema with common fields for all group types.
    """

    all_user_memberships_and_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="All user memberships and ownerships"
    )
    active_user_memberships_and_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active user memberships and ownerships"
    )
    active_user_memberships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active user memberships (not ownerships)"
    )
    active_user_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active user ownerships"
    )

    all_group_tags: list["OktaGroupTagMap"] = Field(default_factory=list, description="All tag mappings for this group")
    active_group_tags: list["OktaGroupTagMap"] = Field(
        default_factory=list, description="Active tag mappings for this group"
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


class OktaGroupRead(NonRoleGroupReadBase):
    """
    Standard Okta group schema.
    This is the base group type.
    """

    type: Literal["okta_group"] = Field("okta_group", description="Group type discriminator")

    all_role_mappings: list["RoleGroupMap"] = Field(default_factory=list, description="All role mappings to this group")
    active_role_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Active role mappings to this group"
    )
    active_role_member_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Active role member mappings to this group"
    )
    active_role_owner_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Active role owner mappings to this group"
    )

    active_non_role_user_memberships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active non-role user memberships"
    )
    active_non_role_user_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active non-role user ownerships"
    )


class RoleGroupRead(BaseReadSchema, GroupBase):
    """
    Role group schema.
    Role groups grant permissions to other groups.
    """

    type: Literal["role_group"] = Field("role_group", description="Group type discriminator")

    # Role-specific relationships - groups this role grants access to
    all_role_associated_group_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Groups that this role grants access to"
    )
    active_role_associated_group_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Groups that this role grants access to"
    )
    active_role_associated_group_member_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Groups that this role grants member access to"
    )
    active_role_associated_group_owner_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Groups that this role grants owner access to"
    )


class AppGroupRead(NonRoleGroupReadBase):
    """
    App group schema.
    App groups are associated with applications.
    """

    type: Literal["app_group"] = Field("app_group", description="Group type discriminator")
    app_id: str = Field(..., description="ID of the associated application")
    is_owner: bool = Field(False, description="Whether this is an app owner group")

    # App-specific relationships
    app: "AppRead | None" = Field(None, description="Associated application")

    all_role_mappings: list["RoleGroupMap"] = Field(default_factory=list, description="All role mappings to this group")
    active_role_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Active role mappings to this group"
    )
    active_role_member_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Active role member mappings to this group"
    )
    active_role_owner_mappings: list["RoleGroupMap"] = Field(
        default_factory=list, description="Active role owner mappings to this group"
    )

    active_non_role_user_memberships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active non-role user memberships"
    )
    active_non_role_user_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active non-role user ownerships"
    )


# Discriminated Union for all group types
def get_group_type(v: Any) -> str:
    """Discriminator function to determine group type."""
    if isinstance(v, dict):
        return v.get("type", "okta_group")
    return getattr(v, "type", "okta_group")


GroupRead = OktaGroupRead | RoleGroupRead | AppGroupRead
GroupReadAnnotated = Field(discriminator=Discriminator(get_group_type))


# Create/Update schemas for groups


class OktaGroupCreate(BaseCreateSchema, GroupBase):
    """Schema for creating standard Okta groups."""

    type: Literal["okta_group"] = Field("okta_group", description="Group type")

    # Tags for creation (load-only)
    tags_to_add: list[str] = Field(default_factory=list, description="Tag IDs to add to the group")


class RoleGroupCreate(BaseCreateSchema, GroupBase):
    """Schema for creating role groups."""

    type: Literal["role_group"] = Field("role_group", description="Group type")

    # Role groups have name prefix requirements
    name: str = Field(..., min_length=1, max_length=255, description="Role group name (must start with 'Role-')")


class AppGroupCreate(BaseCreateSchema, GroupBase):
    """Schema for creating app groups."""

    type: Literal["app_group"] = Field("app_group", description="Group type")
    app_id: str = Field(..., description="ID of the associated application")
    is_owner: bool = Field(False, description="Whether this is an app owner group")

    # App groups have name prefix requirements
    name: str = Field(..., min_length=1, max_length=255, description="App group name (must start with 'App-')")


# Union for create operations
GroupCreate = OktaGroupCreate | RoleGroupCreate | AppGroupCreate
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
    tags_to_add: list[str] = Field(default_factory=list, description="Tag IDs to add to the group")
    tags_to_remove: list[str] = Field(default_factory=list, description="Tag IDs to remove from the group")


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

    members: list["UserSummary"] = Field(default_factory=list, description="Group members")
    owners: list["UserSummary"] = Field(default_factory=list, description="Group owners")
    total_members: int = Field(0, description="Total number of members")
    total_owners: int = Field(0, description="Total number of owners")


class RoleGroupMap(BaseReadSchema):
    """
    Schema for role-group mappings.
    Maps to RoleGroupMap model - represents which groups a role has access to.
    """

    role_group_id: str = Field(..., description="Role group ID")
    group_id: str = Field(..., description="Target group ID")
    is_owner: bool = Field(False, description="Does this role grant ownership of the group")
    ended_at: datetime | None = Field(None, description="When this mapping ended")
    created_actor_id: str | None = Field(None, description="ID of user who created this mapping")
    ended_actor_id: str | None = Field(None, description="ID of user who ended this mapping")

    # Forward references to avoid circular imports
    role_group: RoleGroupRead | None = Field(None, description="Role group")
    group: "GroupRead | None" = Field(None, description="Target group")
    created_actor: "UserSummary | None" = Field(None, description="User who created this mapping")
    ended_actor: "UserSummary | None" = Field(None, description="User who ended this mapping")

    # Membership relationships through this role mapping
    all_group_memberships_and_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="All user memberships created through this role mapping"
    )
    active_group_memberships_and_ownerships: list["UserGroupMember"] = Field(
        default_factory=list, description="Current active group memberships and ownerships"
    )


class OktaGroupTagMap(BaseReadSchema):
    """
    Schema for group-tag mappings.
    Maps to OktaGroupTagMap model - represents which tags are applied to a group.
    """

    group_id: str = Field(..., description="Group ID")
    tag_id: str = Field(..., description="Tag ID")
    ended_at: datetime | None = Field(None, description="When this tag mapping ended")
    app_tag_map_id: int | None = Field(None, description="Associated app tag mapping ID")

    group: "GroupRead | None" = Field(None, description="Tagged group")
    active_group: "GroupRead | None" = Field(None, description="Active tagged group")

    tag: "TagRead | None" = Field(None, description="Applied tag")
    active_tag: "TagRead | None" = Field(None, description="Active applied tag")
    enabled_active_tag: "TagRead | None" = Field(None, description="Enabled active applied tag")

    app_tag_mapping: "AppTagMap | None" = Field(None, description="Associated app tag mapping")
    active_app_tag_mapping: "AppTagMap | None" = Field(None, description="Active associated app tag mapping")


class AppRead(BaseSchema):
    """Placeholder for App schema - to be implemented."""

    id: str
    name: str


# Type aliases for JSON fields
ConstraintsDict = dict[str, Any]


class TagBase(BaseSchema):
    """
    Base tag schema with common fields.
    """

    name: str = Field(..., min_length=1, max_length=255, description="Tag name")
    description: str = Field("", max_length=1024, description="Tag description")
    enabled: bool = Field(True, description="Whether the tag is enabled")
    constraints: ConstraintsDict = Field(default_factory=dict, description="Tag constraints configuration")


class TagSummary(BaseSchema):
    """
    Minimal tag information for use in relationships.
    """

    id: str = Field(..., description="Tag ID")
    name: str = Field(..., description="Tag name")
    description: str = Field("", description="Tag description")
    enabled: bool = Field(True, description="Whether the tag is enabled")
    deleted_at: datetime | None = Field(None, description="When tag was deleted")


class TagRead(BaseReadSchema, TagBase):
    """
    Schema for reading tags with full details.
    """

    # Relationships - would include app and group mappings
    # apps_count: int = Field(0, description="Number of apps using this tag")
    # groups_count: int = Field(0, description="Number of groups using this tag")


class TagCreate(BaseCreateSchema, TagBase):
    """
    Schema for creating tags.
    """

    # Optional apps to associate with the tag during creation
    app_ids: list[str] = Field(default_factory=list, description="App IDs to associate with this tag")


class TagUpdate(BaseUpdateSchema):
    """
    Schema for updating tags.
    All fields are optional to support partial updates.
    """

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1024)
    enabled: bool | None = None
    constraints: ConstraintsDict | None = None

    # Apps to add/remove during update
    app_ids_to_add: list[str] = Field(default_factory=list, description="App IDs to add to this tag")
    app_ids_to_remove: list[str] = Field(default_factory=list, description="App IDs to remove from this tag")


class TagList(BaseReadSchema, TagBase):
    """
    Simplified schema for tag lists.
    Used for search results and basic tag listings.
    """

    # Include summary counts for list view
    apps_count: int = Field(0, description="Number of apps using this tag")
    groups_count: int = Field(0, description="Number of groups using this tag")


class TagSearch(BaseSchema):
    """Schema for tag search parameters."""

    q: str | None = Field(None, description="Search query across name and description")
    enabled: bool | None = Field(None, description="Filter by enabled status")
    app_id: str | None = Field(None, description="Filter by associated app")
    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(25, ge=1, le=100, description="Items per page")


# Tag mapping schemas (for associations between tags and apps/groups)


class TagMappingBase(BaseSchema):
    """
    Base schema for tag mappings (relationships between tags and other entities).
    """

    tag_id: str = Field(..., description="Tag ID")
    created_at: datetime = Field(..., description="When the mapping was created")
    ended_at: datetime | None = Field(None, description="When the mapping ended")


class AppTagMappingRead(BaseReadSchema, TagMappingBase):
    """
    Schema for app-tag mappings.
    """

    app_id: str = Field(..., description="App ID")

    # Relationships
    tag: TagSummary | None = Field(None, description="Associated tag")
    app: "AppSummary | None" = Field(None, description="Associated app")


class GroupTagMappingRead(BaseReadSchema, TagMappingBase):
    """
    Schema for group-tag mappings.
    """

    group_id: str = Field(..., description="Group ID")
    app_tag_map_id: int | None = Field(None, description="Associated app tag mapping ID")

    # Relationships
    tag: TagSummary | None = Field(None, description="Associated tag")
    group: "GroupSummary | None" = Field(None, description="Associated group")
    app_tag_mapping: AppTagMappingRead | None = Field(None, description="Associated app tag mapping")


class TagMappingCreate(BaseCreateSchema):
    """
    Schema for creating tag mappings.
    """

    entity_type: str = Field(..., description="Type of entity (app or group)")
    entity_id: str = Field(..., description="ID of the entity to map")
    tag_id: str = Field(..., description="Tag ID")


class TagMappingList(BaseSchema):
    """
    Schema for listing tag mappings with entity details.
    """

    id: int = Field(..., description="Mapping ID")
    tag: TagSummary = Field(..., description="Associated tag")
    entity_type: str = Field(..., description="Type of entity")
    entity_id: str = Field(..., description="Entity ID")
    entity_name: str = Field("", description="Entity name")
    created_at: datetime = Field(..., description="When mapping was created")
    ended_at: datetime | None = Field(None, description="When mapping ended")
