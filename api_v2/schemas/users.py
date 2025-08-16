"""
Pydantic schemas for User-related models.
Converts from Marshmallow OktaUserSchema to Pydantic models.

Note: Users are managed by Okta and synchronized, not created/updated via API.
Only read operations are supported.
"""

from __future__ import annotations

from datetime import datetime

# Using Python 3.10+ union syntax (str | None) instead of typing.Optional
from pydantic import EmailStr, Field

from api_v2.schemas.base import BaseReadSchema, BaseSchema, ProfileDict


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
