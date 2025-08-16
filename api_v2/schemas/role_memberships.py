"""
FastAPI/Pydantic role membership schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import Field, field_validator

from api_v2.schemas.base import BaseSchema


class RoleMemberUpdate(BaseSchema):
    """
    Schema for updating role group memberships.
    Maps to RoleMemberSchema from Marshmallow.
    """
    # Read-only fields (dump_only in Marshmallow)
    groups_in_role: Optional[List[str]] = Field(None, description="Current groups in this role")
    groups_owned_by_role: Optional[List[str]] = Field(None, description="Current groups owned by this role")
    
    # Fields for adding/removing groups (load_only in Marshmallow)
    groups_to_add: List[str] = Field(
        ...,
        description="Group IDs to add to the role",
        min_length=0
    )
    groups_should_expire: Optional[List[int]] = Field(
        None,
        description="List of group indices that should expire"
    )
    owner_groups_to_add: List[str] = Field(
        ...,
        description="Group IDs to add as owner groups to the role",
        min_length=0
    )
    groups_to_remove: List[str] = Field(
        ...,
        description="Group IDs to remove from the role",
        min_length=0
    )
    owner_groups_should_expire: Optional[List[int]] = Field(
        None,
        description="List of owner group indices that should expire"
    )
    owner_groups_to_remove: List[str] = Field(
        ...,
        description="Group IDs to remove from owner groups",
        min_length=0
    )
    created_reason: Optional[str] = Field(
        None,
        max_length=1024,
        description="Reason for membership changes"
    )
    groups_added_ending_at: Optional[datetime] = Field(
        None,
        description="When added groups' access should end (must be in future)"
    )
    
    @field_validator(
        "groups_to_add", 
        "owner_groups_to_add", 
        "groups_to_remove", 
        "owner_groups_to_remove"
    )
    @classmethod
    def validate_group_id_length(cls, v: List[str]) -> List[str]:
        """Validate that group IDs are exactly 20 characters."""
        for group_id in v:
            if len(group_id) != 20:
                raise ValueError(f"Group ID must be exactly 20 characters, got: {group_id}")
        return v
    
    @field_validator("groups_added_ending_at")
    @classmethod
    def validate_future_date(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Validate that ending datetime is in the future."""
        if v is not None and v < datetime.now():
            raise ValueError("Ended at datetime for add groups must be in the future")
        return v


class RoleMemberRead(BaseSchema):
    """
    Schema for reading role membership information.
    """
    groups_in_role: List[str] = Field(default_factory=list, description="Groups in this role")
    groups_owned_by_role: List[str] = Field(default_factory=list, description="Groups owned by this role")