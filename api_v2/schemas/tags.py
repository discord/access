"""
Pydantic schemas for Tag-related models.
Converts from Marshmallow tag schemas to Pydantic models.
"""

from datetime import datetime
from typing import Any

from pydantic import Field

from api_v2.schemas.base import BaseCreateSchema, BaseReadSchema, BaseSchema, BaseUpdateSchema

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
