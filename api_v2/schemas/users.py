"""
Pydantic schemas for User-related models.
Converts from Marshmallow OktaUserSchema to Pydantic models.

Note: Users are managed by Okta and synchronized, not created/updated via API.
Only read operations are supported.
"""
from datetime import datetime
# Using Python 3.10+ union syntax (str | None) instead of typing.Optional
from pydantic import Field, EmailStr

from api_v2.schemas.base import BaseSchema, BaseReadSchema, ProfileDict


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
    
    # Membership relationships - these will be populated by the API layer
    # Temporarily commented out until we implement membership schemas
    # active_group_memberships: List['UserGroupMembershipRead'] = Field(
    #     default_factory=list,
    #     description="Current active group memberships (not ownerships)"
    # )
    # active_group_ownerships: List['UserGroupMembershipRead'] = Field(
    #     default_factory=list,
    #     description="Current active group ownerships"
    # )


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


# Membership relationships will be added in a future iteration