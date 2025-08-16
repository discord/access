"""
FastAPI/Pydantic group membership schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import Field, field_validator, ValidationError

from api_v2.schemas.base import BaseSchema


class GroupMemberUpdate(BaseSchema):
    """
    Schema for updating group memberships.
    Maps to GroupMemberSchema from Marshmallow.
    """
    # Read-only fields (dump_only in Marshmallow)
    members: Optional[List[str]] = Field(None, description="Current group members")
    owners: Optional[List[str]] = Field(None, description="Current group owners")
    
    # Fields for adding members/owners (load_only in Marshmallow)
    members_to_add: List[str] = Field(
        ..., 
        description="User IDs to add as members",
        min_length=0
    )
    owners_to_add: List[str] = Field(
        ...,
        description="User IDs to add as owners",
        min_length=0
    )
    members_should_expire: Optional[List[int]] = Field(
        None,
        description="List of member indices that should expire"
    )
    owners_should_expire: Optional[List[int]] = Field(
        None,
        description="List of owner indices that should expire"
    )
    members_to_remove: List[str] = Field(
        ...,
        description="User IDs to remove from members",
        min_length=0
    )
    owners_to_remove: List[str] = Field(
        ...,
        description="User IDs to remove from owners", 
        min_length=0
    )
    created_reason: Optional[str] = Field(
        None,
        max_length=1024,
        description="Reason for membership changes"
    )
    users_added_ending_at: Optional[datetime] = Field(
        None,
        description="When added users' access should end (must be in future)"
    )
    
    @field_validator("members_to_add", "owners_to_add", "members_to_remove", "owners_to_remove")
    @classmethod
    def validate_user_id_length(cls, v: List[str]) -> List[str]:
        """Validate that user IDs are exactly 20 characters."""
        for user_id in v:
            if len(user_id) != 20:
                raise ValueError(f"User ID must be exactly 20 characters, got: {user_id}")
        return v
    
    @field_validator("users_added_ending_at")
    @classmethod
    def validate_future_date(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Validate that ending datetime is in the future."""
        if v is not None and v < datetime.now():
            raise ValueError("Ended at datetime for add users must be in the future")
        return v


class GroupMemberRead(BaseSchema):
    """
    Schema for reading group membership information.
    """
    members: List[str] = Field(default_factory=list, description="Current group members")
    owners: List[str] = Field(default_factory=list, description="Current group owners")