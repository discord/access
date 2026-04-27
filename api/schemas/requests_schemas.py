"""Pydantic models for access/role/group requests and request/response bodies.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.schemas.core_schemas import (
    AppDetail,
    GroupRef,
    OktaUserSummary,
    OktaUserGroupMemberDetail,
)
from api.schemas.rfc822 import RFC822Datetime, RFC822DatetimeOpt


# --- Access requests --------------------------------------------------------


class AccessRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requester_user_id: Optional[str] = None
    requested_group_id: Optional[str] = None
    request_ownership: bool = False
    request_reason: Optional[str] = ""
    request_ending_at: RFC822DatetimeOpt = None
    status: str
    resolver_user_id: Optional[str] = None
    resolution_reason: Optional[str] = ""
    resolved_at: RFC822DatetimeOpt = None
    approval_ending_at: RFC822DatetimeOpt = None
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    requester: Optional[OktaUserSummary] = None
    active_requester: Optional[OktaUserSummary] = None
    requested_group: Optional[GroupRef] = None
    active_requested_group: Optional[GroupRef] = None
    resolver: Optional[OktaUserSummary] = None
    active_resolver: Optional[OktaUserSummary] = None


class CreateAccessRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    group_id: str
    group_owner: bool = False
    reason: Optional[str] = ""
    ending_at: Optional[RFC822DatetimeOpt] = None


class ResolveAccessRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved: bool
    reason: Optional[str] = ""
    ending_at: Optional[RFC822DatetimeOpt] = None


# --- Role requests ----------------------------------------------------------


class RoleRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requester_user_id: Optional[str] = None
    requester_role_id: Optional[str] = None
    requested_group_id: Optional[str] = None
    request_ownership: bool = False
    request_reason: Optional[str] = ""
    request_ending_at: RFC822DatetimeOpt = None
    status: str
    resolver_user_id: Optional[str] = None
    resolution_reason: Optional[str] = ""
    resolved_at: RFC822DatetimeOpt = None
    approval_ending_at: RFC822DatetimeOpt = None
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    requester: Optional[OktaUserSummary] = None
    requester_role: Optional[GroupRef] = None
    active_requester_role: Optional[GroupRef] = None
    requested_group: Optional[GroupRef] = None
    active_requested_group: Optional[GroupRef] = None
    resolver: Optional[OktaUserSummary] = None
    active_resolver: Optional[OktaUserSummary] = None


class CreateRoleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role_id: str
    group_id: str
    group_owner: bool = False
    reason: Optional[str] = ""
    ending_at: Optional[RFC822DatetimeOpt] = None


class ResolveRoleRequest(ResolveAccessRequest):
    pass


# --- Group requests ---------------------------------------------------------


class GroupRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    requester_user_id: Optional[str] = None
    # The legacy column names use `requested_*` and `resolved_*`, mirrored
    # exactly here so the React frontend's apiSchemas don't have to change.
    requested_group_name: Optional[str] = None
    requested_group_description: Optional[str] = ""
    requested_group_type: Optional[str] = None
    requested_app_id: Optional[str] = None
    requested_group_tags: list[str] = Field(default_factory=list)
    requested_ownership_ending_at: RFC822DatetimeOpt = None
    request_reason: Optional[str] = ""
    resolved_group_name: Optional[str] = ""
    resolved_group_description: Optional[str] = ""
    resolved_group_type: Optional[str] = ""
    resolved_app_id: Optional[str] = None
    resolved_group_tags: list[str] = Field(default_factory=list)
    resolved_ownership_ending_at: RFC822DatetimeOpt = None
    resolution_reason: Optional[str] = ""
    resolved_at: RFC822DatetimeOpt = None
    approved_group_id: Optional[str] = None
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    requester: Optional[OktaUserSummary] = None
    active_requester: Optional[OktaUserSummary] = None
    resolver: Optional[OktaUserSummary] = None
    active_resolver: Optional[OktaUserSummary] = None
    approved_group: Optional[GroupRef] = None

    @field_validator("requested_group_tags", "resolved_group_tags", mode="before")
    @classmethod
    def _coerce_tag_list(cls, v: Any) -> Any:
        # Legacy rows where the JSON column was incorrectly persisted as `{}`
        # because of sqlalchemy_json's dict-default behavior. Treat any
        # non-list value as an empty list.
        if v is None or not isinstance(v, list):
            return []
        return v


class CreateGroupRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    group_type: str
    group_name: str
    app_id: Optional[str] = None
    reason: Optional[str] = ""


class ResolveGroupRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved: bool
    reason: Optional[str] = ""


# --- Group/Role membership editor payloads ----------------------------------


class GroupMember(BaseModel):
    """Used by GET/PUT /api/groups/{id}/members.

    `members_should_expire` and `owners_should_expire` are lists of
    OktaUserGroupMember row ids (integers). The other lists are user ids
    (strings).
    """
    model_config = ConfigDict(extra="ignore")
    members: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    members_to_add: list[str] = Field(default_factory=list)
    members_to_remove: list[str] = Field(default_factory=list)
    owners_to_add: list[str] = Field(default_factory=list)
    owners_to_remove: list[str] = Field(default_factory=list)
    members_should_expire: list[int] = Field(default_factory=list)
    owners_should_expire: list[int] = Field(default_factory=list)
    users_added_ending_at: RFC822DatetimeOpt = None
    created_reason: Optional[str] = ""


class RoleMember(BaseModel):
    """Used by GET/PUT /api/roles/{id}/members (role-to-group mappings).

    `groups_should_expire` and `owner_groups_should_expire` are RoleGroupMap
    row ids (integers). The other lists are group ids (strings).
    """
    model_config = ConfigDict(extra="ignore")
    groups: list[Any] = Field(default_factory=list)
    owner_groups: list[Any] = Field(default_factory=list)
    groups_to_add: list[str] = Field(default_factory=list)
    groups_to_remove: list[str] = Field(default_factory=list)
    owner_groups_to_add: list[str] = Field(default_factory=list)
    owner_groups_to_remove: list[str] = Field(default_factory=list)
    groups_should_expire: list[int] = Field(default_factory=list)
    owner_groups_should_expire: list[int] = Field(default_factory=list)
    groups_added_ending_at: RFC822DatetimeOpt = None
    created_reason: Optional[str] = ""
