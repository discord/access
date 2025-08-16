"""
Pydantic schemas for AccessRequest and RoleRequest models.
Converts from Marshmallow request schemas to Pydantic models.
"""
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import Field, field_validator, model_validator

from api_v2.schemas.base import BaseSchema, BaseReadSchema, BaseCreateSchema, BaseUpdateSchema
from api_v2.schemas.users import UserSummary
from api_v2.schemas.groups import GroupSummary, GroupRead


class AccessRequestStatus(str, Enum):
    """
    Access request status enum.
    Maps to AccessRequestStatus in the database model.
    """
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AccessRequestBase(BaseSchema):
    """
    Base access request schema with common fields.
    """
    status: AccessRequestStatus = Field(
        AccessRequestStatus.PENDING,
        description="Request status"
    )
    requester_user_id: str = Field(..., description="ID of the user making the request")
    requested_group_id: str = Field(..., description="ID of the requested group") 
    request_ownership: bool = Field(False, description="Whether requesting ownership permissions")
    request_reason: str = Field("", max_length=1024, description="Reason for the request")
    request_ending_at: datetime | None = Field(None, description="When the access should end")
    
    # Resolution fields
    resolved_at: datetime | None = Field(None, description="When the request was resolved")
    resolver_user_id: str | None = Field(None, description="ID of the user who resolved the request")
    resolution_reason: str = Field("", max_length=1024, description="Reason for approval/rejection")


class AccessRequestRead(BaseReadSchema, AccessRequestBase):
    """
    Schema for reading access requests with relationships.
    """
    # User relationships
    requester: UserSummary | None = Field(None, description="User making the request")
    active_requester: UserSummary | None = Field(None, description="Active requester (not deleted)")
    resolver: UserSummary | None = Field(None, description="User who resolved the request")
    active_resolver: UserSummary | None = Field(None, description="Active resolver (not deleted)")
    
    # Group relationships
    requested_group: GroupRead | None = Field(None, description="Requested group")
    active_requested_group: GroupRead | None = Field(None, description="Active requested group (not deleted)")
    
    # Membership relationship (if approved)
    # approved_membership: UserGroupMembershipRead | None = Field(None, description="Approved membership")


class AccessRequestCreate(BaseCreateSchema):
    """
    Schema for creating access requests.
    """
    group_id: str = Field(..., min_length=20, max_length=20, description="Group ID (20 characters)")
    group_owner: bool = Field(False, description="Request ownership permissions")
    reason: str | None = Field(None, max_length=1024, description="Reason for the request")
    ending_at: datetime | None = Field(None, description="When the access should end")
    
    @field_validator('ending_at')
    @classmethod
    def ending_at_must_be_future(cls, v: datetime | None) -> datetime | None:
        """Validate that ending_at is in the future."""
        if v is not None and v < datetime.now():
            raise ValueError("Ending datetime must be in the future")
        return v


class AccessRequestResolve(BaseSchema):
    """
    Schema for resolving access requests (approve/reject).
    """
    approved: bool = Field(..., description="Whether to approve the request")
    reason: str | None = Field(None, max_length=1024, description="Reason for approval/rejection")
    ending_at: datetime | None = Field(None, description="When the access should end (if approved)")
    
    @field_validator('ending_at')
    @classmethod
    def ending_at_must_be_future(cls, v: datetime | None) -> datetime | None:
        """Validate that ending_at is in the future."""
        if v is not None and v < datetime.now():
            raise ValueError("Ending datetime must be in the future")
        return v


class AccessRequestList(BaseReadSchema, AccessRequestBase):
    """
    Simplified schema for access request lists.
    """
    # Include basic user/group info without full nesting
    requester_email: str | None = Field(None, description="Email of requester")
    requested_group_name: str | None = Field(None, description="Name of requested group")


# Role Request Schemas

class RoleRequestBase(BaseSchema):
    """
    Base role request schema with common fields.
    """
    status: AccessRequestStatus = Field(
        AccessRequestStatus.PENDING,
        description="Request status"
    )
    requester_user_id: str = Field(..., description="ID of the user making the request")
    requester_role_id: str = Field(..., description="ID of the requester's role") 
    requested_group_id: str = Field(..., description="ID of the requested group")
    request_ownership: bool = Field(False, description="Whether requesting ownership permissions")
    request_reason: str = Field("", max_length=1024, description="Reason for the request")
    request_ending_at: datetime | None = Field(None, description="When the access should end")
    
    # Resolution fields
    resolved_at: datetime | None = Field(None, description="When the request was resolved")
    resolver_user_id: str | None = Field(None, description="ID of the user who resolved the request")
    resolution_reason: str = Field("", max_length=1024, description="Reason for approval/rejection")


class RoleRequestRead(BaseReadSchema, RoleRequestBase):
    """
    Schema for reading role requests with relationships.
    """
    # User relationships
    requester: UserSummary | None = Field(None, description="User making the request")
    active_requester: UserSummary | None = Field(None, description="Active requester (not deleted)")
    resolver: UserSummary | None = Field(None, description="User who resolved the request")
    active_resolver: UserSummary | None = Field(None, description="Active resolver (not deleted)")
    
    # Role relationships
    requester_role: GroupRead | None = Field(None, description="Requester's role group")
    active_requester_role: GroupRead | None = Field(None, description="Active requester role (not deleted)")
    
    # Group relationships
    requested_group: GroupRead | None = Field(None, description="Requested group")
    active_requested_group: GroupRead | None = Field(None, description="Active requested group (not deleted)")


class RoleRequestCreate(BaseCreateSchema):
    """
    Schema for creating role requests.
    """
    role_id: str = Field(..., min_length=20, max_length=20, description="Role ID (20 characters)")
    group_id: str = Field(..., min_length=20, max_length=20, description="Group ID (20 characters)")
    group_owner: bool = Field(False, description="Request ownership permissions")
    reason: str | None = Field(None, max_length=1024, description="Reason for the request")
    ending_at: datetime | None = Field(None, description="When the access should end")
    
    @field_validator('ending_at')
    @classmethod
    def ending_at_must_be_future(cls, v: datetime | None) -> datetime | None:
        """Validate that ending_at is in the future."""
        if v is not None and v < datetime.now():
            raise ValueError("Ending datetime must be in the future")
        return v


class RoleRequestResolve(BaseSchema):
    """
    Schema for resolving role requests (approve/reject).
    """
    approved: bool = Field(..., description="Whether to approve the request")
    reason: str | None = Field(None, max_length=1024, description="Reason for approval/rejection")
    ending_at: datetime | None = Field(None, description="When the access should end (if approved)")
    
    @field_validator('ending_at')
    @classmethod
    def ending_at_must_be_future(cls, v: datetime | None) -> datetime | None:
        """Validate that ending_at is in the future."""
        if v is not None and v < datetime.now():
            raise ValueError("Ending datetime must be in the future")
        return v


class RoleRequestList(BaseReadSchema, RoleRequestBase):
    """
    Simplified schema for role request lists.
    """
    # Include basic user/group info without full nesting
    requester_email: str | None = Field(None, description="Email of requester")
    requester_role_name: str | None = Field(None, description="Name of requester's role")
    requested_group_name: str | None = Field(None, description="Name of requested group")


# Search and pagination schemas

class AccessRequestSearch(BaseSchema):
    """Schema for access request search parameters."""
    q: str | None = Field(None, description="Search query")
    status: AccessRequestStatus | None = Field(None, description="Filter by status")
    requester_user_id: str | None = Field(None, description="Filter by requester")
    requested_group_id: str | None = Field(None, description="Filter by requested group")
    resolver_user_id: str | None = Field(None, description="Filter by resolver")
    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(25, ge=1, le=100, description="Items per page")


class RoleRequestSearch(BaseSchema):
    """Schema for role request search parameters."""
    q: str | None = Field(None, description="Search query")
    status: AccessRequestStatus | None = Field(None, description="Filter by status")
    requester_user_id: str | None = Field(None, description="Filter by requester")
    requester_role_id: str | None = Field(None, description="Filter by requester role")
    requested_group_id: str | None = Field(None, description="Filter by requested group")
    resolver_user_id: str | None = Field(None, description="Filter by resolver")
    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(25, ge=1, le=100, description="Items per page")