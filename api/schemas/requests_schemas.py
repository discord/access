"""Pydantic models for access/role/group requests and request/response bodies.

Naming: `*Detail` is the response shape; `*Body` (e.g. `CreateAccessRequestBody`)
is the request body that the router parses out of `Body(...)`. The `Body`
suffix avoids a naming collision with the operation classes
(`CreateAccessRequest`, `ResolveAccessRequest`, …) imported from
`api.operations`.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from api.schemas.core_schemas import (
    GroupRef,
    OktaUserSummary,
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


class CreateAccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    group_id: str
    group_owner: bool = False
    reason: Optional[str] = ""
    ending_at: Optional[RFC822DatetimeOpt] = None


class ResolveAccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # `StrictBool` rejects `"false"` / `"true"` strings outright. The
    # legacy Marshmallow schema declared `fields.Boolean(required=True)`
    # which had the same behaviour; Python's `bool()` (which the migration
    # briefly used) interprets every non-empty string as True and silently
    # turns "false" into an APPROVED outcome.
    approved: StrictBool
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


class CreateRoleRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role_id: str
    group_id: str
    group_owner: bool = False
    reason: Optional[str] = ""
    ending_at: Optional[RFC822DatetimeOpt] = None


class ResolveRoleRequestBody(ResolveAccessRequestBody):
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


class CreateGroupRequestBody(BaseModel):
    """Body for POST /api/group-requests. Field names mirror the legacy
    SQLAlchemy column names so the React frontend's apiSchemas don't
    have to change."""

    model_config = ConfigDict(extra="ignore")
    requested_group_name: Optional[str] = Field(default=None)
    requested_group_description: Optional[str] = ""
    requested_group_type: Optional[str] = None
    requested_app_id: Optional[str] = None
    # `app_id` is the legacy name that some clients send; aliased to
    # `requested_app_id`.
    app_id: Optional[str] = Field(default=None, exclude=True)
    requested_group_tags: list[str] = Field(default_factory=list)
    requested_ownership_ending_at: Optional[RFC822DatetimeOpt] = None
    request_reason: Optional[str] = ""
    # Legacy aliases the React form sends — fold into the resolved fields.
    group_name: Optional[str] = Field(default=None, exclude=True)
    group_type: Optional[str] = Field(default=None, exclude=True)
    reason: Optional[str] = Field(default=None, exclude=True)


class ResolveGroupRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved: StrictBool
    resolution_reason: Optional[str] = ""
    reason: Optional[str] = ""
    resolved_group_name: Optional[str] = None
    resolved_group_description: Optional[str] = None
    resolved_group_type: Optional[str] = None
    resolved_app_id: Optional[str] = None
    resolved_group_tags: Optional[list[str]] = None
    resolved_ownership_ending_at: Optional[RFC822DatetimeOpt] = None


# --- Tags -------------------------------------------------------------------


class CreateTagBody(BaseModel):
    """Body for POST /api/tags."""

    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    constraints: Optional[dict[str, Any]] = None
    enabled: bool = True


class UpdateTagBody(BaseModel):
    """Body for PUT /api/tags/{id}. All fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    description: Optional[str] = None
    constraints: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None


# --- Apps -------------------------------------------------------------------


class _InitialAppGroupBody(BaseModel):
    """Body shape for an entry in `initial_additional_app_groups`."""

    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = ""
    type: Optional[str] = "app_group"
    plugin_data: Optional[dict[str, Any]] = None
    tags_to_add: Optional[list[str]] = None


class CreateAppBody(BaseModel):
    """Body for POST /api/apps."""

    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    initial_owner_id: Optional[str] = None
    initial_owner_role_ids: Optional[list[str]] = None
    initial_additional_app_groups: Optional[list[_InitialAppGroupBody]] = None
    tags_to_add: Optional[list[str]] = None
    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None


class UpdateAppBody(BaseModel):
    """Body for PUT /api/apps/{id}. All fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    description: Optional[str] = None
    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    tags_to_add: Optional[list[str]] = None
    tags_to_remove: Optional[list[str]] = None


# --- Groups -----------------------------------------------------------------


class CreateGroupBody(BaseModel):
    """Body for POST /api/groups. Type-discriminated subtype information
    (`type`, `app_id`, `is_owner`) is captured here so the handler can
    branch on it rather than re-parsing the dict."""

    model_config = ConfigDict(extra="ignore")
    type: str
    name: str
    description: Optional[str] = None
    is_owner: bool = False
    app_id: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    tags_to_add: Optional[list[str]] = None


class UpdateGroupBody(BaseModel):
    """Body for PUT /api/groups/{id}. All fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    type: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    is_owner: Optional[bool] = None
    app_id: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    tags_to_add: Optional[list[str]] = None
    tags_to_remove: Optional[list[str]] = None


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
