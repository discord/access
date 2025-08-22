"""
FastAPI/Pydantic audit log schemas.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from api_v2.schemas import TagRead, UserSummary
from api_v2.schemas.base import BaseReadSchema, BaseSchema


class EventType(str, Enum):
    """Audit log event types."""

    ACCESS_APPROVE = "ACCESS_APPROVE"
    ACCESS_CREATE = "ACCESS_CREATE"
    ACCESS_REJECT = "ACCESS_REJECT"
    APP_CREATE = "APP_CREATE"
    APP_DELETE = "APP_DELETE"
    APP_MODIFY_NAME = "APP_MODIFY_NAME"
    APP_MODIFY_TAG = "APP_MODIFY_TAG"
    GROUP_CREATE = "GROUP_CREATE"
    GROUP_DELETE = "GROUP_DELETE"
    GROUP_MODIFY_NAME = "GROUP_MODIFY_NAME"
    GROUP_MODIFY_TYPE = "GROUP_MODIFY_TYPE"
    GROUP_MODIFY_TAG = "GROUP_MODIFY_TAG"
    GROUP_MODIFY_USER = "GROUP_MODIFY_USER"
    ROLE_GROUP_MODIFY = "ROLE_GROUP_MODIFY"
    ROLE_REQUEST_APPROVE = "ROLE_REQUEST_APPROVE"
    ROLE_REQUEST_CREATE = "ROLE_REQUEST_CREATE"
    ROLE_REQUEST_REJECT = "ROLE_REQUEST_REJECT"
    TAG_CREATE = "TAG_CREATE"
    TAG_MODIFY = "TAG_MODIFY"
    TAG_DELETE = "TAG_DELETE"


class UserGroupMembershipSummary(BaseSchema):
    """Summary schema for user-group memberships in audit logs."""

    user_id: str = Field(..., description="User ID")
    group_id: str = Field(..., description="Group ID")


class RoleGroupMapSummary(BaseSchema):
    """Summary schema for role-group mappings in audit logs."""

    role_group_id: str = Field(..., description="Role group ID")
    group_id: str = Field(..., description="Group ID")


class AppSummary(BaseSchema):
    """Summary schema for apps in audit logs."""

    id: str = Field(..., description="App ID")
    name: str = Field(..., description="App name")


class GroupSummary(BaseSchema):
    """Summary schema for groups in audit logs."""

    id: str = Field(..., description="Group ID")
    name: str = Field(..., description="Group name")
    type: str = Field(..., description="Group type")
    app: AppSummary | None = Field(None, description="Associated app")


class RoleGroupSummary(BaseSchema):
    """Summary schema for role groups in audit logs."""

    id: str = Field(..., description="Role group ID")
    name: str = Field(..., description="Role group name")


class AccessRequestSummary(BaseSchema):
    """Summary schema for access requests in audit logs."""

    id: str = Field(..., description="Request ID")
    request_reason: str | None = Field(None, description="Request reason")
    request_ending_at: datetime | None = Field(None, description="When request ends")
    request_ownership: bool = Field(False, description="Request ownership")
    resolution_reason: str | None = Field(None, description="Resolution reason")
    approval_ending_at: datetime | None = Field(None, description="When approval ends")


class RoleRequestSummary(BaseSchema):
    """Summary schema for role requests in audit logs."""

    id: str = Field(..., description="Request ID")
    requester_role: RoleGroupSummary | None = Field(None, description="Requester role")
    request_reason: str | None = Field(None, description="Request reason")
    request_ending_at: datetime | None = Field(None, description="When request ends")
    request_ownership: bool = Field(False, description="Request ownership")
    resolution_reason: str | None = Field(None, description="Resolution reason")
    approval_ending_at: datetime | None = Field(None, description="When approval ends")


class AuditLogRead(BaseReadSchema):
    """
    Schema for audit log entries.
    Maps to AuditLog model with comprehensive event tracking.
    """

    event_type: EventType = Field(..., description="Type of audit event")
    user_agent: str | None = Field(None, description="User agent string")
    ip: str | None = Field(None, description="IP address")
    current_user_id: str | None = Field(None, description="Current user ID")
    current_user_email: str | None = Field(None, description="Current user email")

    # Group-related audit fields
    group: GroupSummary | None = Field(None, description="Affected group")
    old_group_name: str | None = Field(None, description="Previous group name")
    old_group_type: str | None = Field(None, description="Previous group type")
    group_owners: list[UserSummary] = Field(default_factory=list, description="Group owners")
    owners_removed_ids_emails: list[UserSummary] = Field(default_factory=list, description="Owners removed")
    owners_added_ids_emails: list[UserSummary] = Field(default_factory=list, description="Owners added")
    owners_should_expire_user_id_group_id: list[UserGroupMembershipSummary] = Field(
        default_factory=list, description="Owner memberships that should expire"
    )
    members_removed_ids_emails: list[UserSummary] = Field(default_factory=list, description="Members removed")
    members_added_ids_emails: list[UserSummary] = Field(default_factory=list, description="Members added")
    members_should_expire_user_id_group_id: list[UserGroupMembershipSummary] = Field(
        default_factory=list, description="Member memberships that should expire"
    )

    # Role-related audit fields
    role: RoleGroupSummary | None = Field(None, description="Affected role")
    groups_added_ending_at: datetime | None = Field(None, description="When added groups end")
    owner_groups_removed_ids_names: list[RoleGroupSummary] = Field(
        default_factory=list, description="Owner groups removed"
    )
    owner_groups_added_ids_names: list[RoleGroupSummary] = Field(default_factory=list, description="Owner groups added")
    owner_groups_should_expire_role_id_group_id: list[RoleGroupMapSummary] = Field(
        default_factory=list, description="Owner group mappings that should expire"
    )
    groups_removed_ids_names: list[RoleGroupSummary] = Field(default_factory=list, description="Groups removed")
    groups_added_ids_names: list[RoleGroupSummary] = Field(default_factory=list, description="Groups added")
    groups_should_expire_role_id_group_id: list[RoleGroupMapSummary] = Field(
        default_factory=list, description="Group mappings that should expire"
    )

    # Request-related audit fields
    request: AccessRequestSummary | None = Field(None, description="Access request")
    role_request: RoleRequestSummary | None = Field(None, description="Role request")
    requester: UserSummary | None = Field(None, description="Requester")

    # App-related audit fields
    app: AppSummary | None = Field(None, description="Affected app")
    old_app_name: str | None = Field(None, description="Previous app name")
    owner_id: str | None = Field(None, description="Owner ID")

    # Tag-related audit fields
    tag: TagRead | None = Field(None, description="Affected tag")
    old_tag: TagRead | None = Field(None, description="Previous tag state")
    tags_added: list[TagRead] = Field(default_factory=list, description="Tags added")
    tags_removed: list[TagRead] = Field(default_factory=list, description="Tags removed")

    @field_validator("*", mode="before")
    @classmethod
    def remove_skip_values(cls, v: Any, info) -> Any:
        """Remove None values except for allowed keys."""
        if hasattr(info, "field_name"):
            field_name = info.field_name
            allowed_keys = ["groups_added_ending_at", "current_user_id", "current_user_email"]
            if v is None and field_name not in allowed_keys:
                return None
        return v


class AuditLogCreate(BaseSchema):
    """
    Schema for creating audit log entries.
    Used internally by the audit logging system.
    """

    event_type: EventType = Field(..., description="Type of audit event")
    user_agent: str | None = Field(None, description="User agent string")
    ip: str | None = Field(None, description="IP address")
    current_user_id: str | None = Field(None, description="Current user ID")
    current_user_email: str | None = Field(None, description="Current user email")

    # Additional fields can be added dynamically based on event type
    additional_data: dict[str, Any] = Field(default_factory=dict, description="Additional event data")
