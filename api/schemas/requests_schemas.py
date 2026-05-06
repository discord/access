"""Pydantic models for access/role/group requests and request/response bodies.

Naming: `*Detail` is the response shape; `*Body` (e.g. `CreateAccessRequestBody`)
is the request body that the router parses out of `Body(...)`. The `Body`
suffix avoids a naming collision with the operation classes
(`CreateAccessRequest`, `ResolveAccessRequest`, …) imported from
`api.operations`.

`CreateGroupBody`, `UpdateGroupBody`, and `CreateGroupRequestBody` are
discriminated unions on the group `type` (or `requested_group_type`),
so the router branches via `isinstance(body, _AppGroupCreateBody)`
rather than re-parsing the dict.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Self, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from api.access_config import get_access_config
from api.config import settings
from api.schemas.core_schemas import (
    GroupRef,
    OktaUserSummary,
)
from api.schemas.rfc822 import RFC822Datetime, RFC822DatetimeOpt

# Anchored at both ends so a name like "Bad Name!" can't slip past on the
# strength of an unanchored prefix match.
_GROUP_NAME_PATTERN_STR = f"^{get_access_config().name_pattern}$"
_GROUP_NAME_MAX_LENGTH = 255
_GROUP_DESC_MAX_LENGTH = 1024

# Apps and Tags do not enforce a name pattern but share the same DB column
# widths as groups (Unicode(255) for `name`, Unicode(1024) for `description`
# — see api/models/core_models.py).
_APP_NAME_MAX_LENGTH = 255
_APP_DESC_MAX_LENGTH = 1024
_TAG_NAME_MAX_LENGTH = 255
_TAG_DESC_MAX_LENGTH = 1024


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


class _GroupRequestBodyBase(BaseModel):
    """Shared fields for the three group-request variants. The discriminator
    field (`requested_group_type`) is declared on each concrete class as a
    `Literal[...]` so Pydantic can dispatch via `Field(discriminator=...)`.
    """

    model_config = ConfigDict(extra="ignore")
    requested_group_name: str = Field(pattern=_GROUP_NAME_PATTERN_STR, min_length=1, max_length=_GROUP_NAME_MAX_LENGTH)
    requested_group_description: Optional[str] = Field(default="", max_length=_GROUP_DESC_MAX_LENGTH)
    requested_group_tags: list[str] = Field(default_factory=list)
    requested_ownership_ending_at: Optional[RFC822DatetimeOpt] = None
    request_reason: Optional[str] = ""

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if not settings.REQUIRE_DESCRIPTIONS:
            return self
        if self.requested_group_description is None or self.requested_group_description == "":
            raise ValueError("Description is required.")
        return self


class _OktaGroupRequestBody(_GroupRequestBodyBase):
    requested_group_type: Literal["okta_group"]


class _RoleGroupRequestBody(_GroupRequestBodyBase):
    requested_group_type: Literal["role_group"]


class _AppGroupRequestBody(_GroupRequestBodyBase):
    requested_group_type: Literal["app_group"]
    requested_app_id: str


CreateGroupRequestBody = Annotated[
    Union[_OktaGroupRequestBody, _RoleGroupRequestBody, _AppGroupRequestBody],
    Field(discriminator="requested_group_type"),
]


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


def _validate_tag_constraints(v: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Reject unknown constraint keys and constraint values that fail the
    per-key validator. Pydantic-side replacement for the previous
    `_validate_constraints` helper in `api/routers/tags.py`."""
    if v is None:
        return None
    # Local import to avoid a model→schema import cycle.
    from api.models import Tag

    valid: dict[str, Any] = {}
    for key, value in v.items():
        if key not in Tag.CONSTRAINTS:
            raise ValueError(f"Unknown constraint: {key}")
        if not Tag.CONSTRAINTS[key].validator(value):
            raise ValueError(f"Invalid value for constraint {key}: {value!r}")
        valid[key] = value
    return valid


class CreateTagBody(BaseModel):
    """Body for POST /api/tags."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=_TAG_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=_TAG_DESC_MAX_LENGTH)
    constraints: Optional[dict[str, Any]] = None
    enabled: bool = True

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self

    @field_validator("constraints")
    @classmethod
    def _check_constraints(cls, v: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return _validate_tag_constraints(v)


class UpdateTagBody(BaseModel):
    """Body for PUT /api/tags/{id}. All fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(default=None, min_length=1, max_length=_TAG_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=_TAG_DESC_MAX_LENGTH)
    constraints: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        # Partial PUTs that don't touch description must not fail.
        if "description" not in self.model_fields_set:
            return self
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self

    @field_validator("constraints")
    @classmethod
    def _check_constraints(cls, v: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return _validate_tag_constraints(v)


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
    name: str = Field(min_length=1, max_length=_APP_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=_APP_DESC_MAX_LENGTH)
    initial_owner_id: Optional[str] = None
    initial_owner_role_ids: Optional[list[str]] = None
    initial_additional_app_groups: Optional[list[_InitialAppGroupBody]] = None
    tags_to_add: Optional[list[str]] = None
    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self

    @model_validator(mode="after")
    def _check_initial_app_group_names(self) -> Self:
        if not self.initial_additional_app_groups:
            return self
        # Local import to avoid model→schema cycles.
        from api.models import AppGroup as _AppGroup

        prefix = f"{_AppGroup.APP_GROUP_NAME_PREFIX}{self.name}{_AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        owner_group_name = f"{prefix}{_AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        for ig in self.initial_additional_app_groups:
            if not ig.name.startswith(prefix):
                raise ValueError(f"Additional app group name must be prefixed with {prefix}")
            if ig.name == owner_group_name:
                raise ValueError(
                    f"Cannot specify {_AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX} group as an additional app group"
                )
        return self


class UpdateAppBody(BaseModel):
    """Body for PUT /api/apps/{id}. All fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(default=None, min_length=1, max_length=_APP_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=_APP_DESC_MAX_LENGTH)
    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    tags_to_add: Optional[list[str]] = None
    tags_to_remove: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if "description" not in self.model_fields_set:
            return self
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self


# --- Groups -----------------------------------------------------------------


class _GroupCreateBodyShared(BaseModel):
    """Non-discriminator fields shared across the three create variants. Each
    concrete variant adds its own `type: Literal[...]` so mypy is happy with
    the per-class discriminator type."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(pattern=_GROUP_NAME_PATTERN_STR, min_length=1, max_length=_GROUP_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=_GROUP_DESC_MAX_LENGTH)
    tags_to_add: list[str] = Field(default_factory=list)
    tags_to_remove: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self


class _OktaGroupCreateBody(_GroupCreateBodyShared):
    type: Literal["okta_group"]


class _RoleGroupCreateBody(_GroupCreateBodyShared):
    type: Literal["role_group"]


class _AppGroupCreateBody(_GroupCreateBodyShared):
    type: Literal["app_group"]
    app_id: Optional[str] = None
    is_owner: bool = False
    plugin_data: Optional[dict[str, Any]] = None


CreateGroupBody = Annotated[
    Union[_OktaGroupCreateBody, _RoleGroupCreateBody, _AppGroupCreateBody],
    Field(discriminator="type"),
]


class _GroupUpdateBodyShared(BaseModel):
    """Non-discriminator fields shared across the three update variants. All
    fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = Field(default=None, pattern=_GROUP_NAME_PATTERN_STR, min_length=1, max_length=_GROUP_NAME_MAX_LENGTH)
    description: Optional[str] = Field(default=None, max_length=_GROUP_DESC_MAX_LENGTH)
    tags_to_add: list[str] = Field(default_factory=list)
    tags_to_remove: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        # Partial PUTs that don't touch description must not fail; only
        # validate when the client provided a value.
        if "description" not in self.model_fields_set:
            return self
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self


class _OktaGroupUpdateBody(_GroupUpdateBodyShared):
    type: Literal["okta_group"]


class _RoleGroupUpdateBody(_GroupUpdateBodyShared):
    type: Literal["role_group"]


class _AppGroupUpdateBody(_GroupUpdateBodyShared):
    type: Literal["app_group"]
    app_id: Optional[str] = None
    is_owner: Optional[bool] = None
    plugin_data: Optional[dict[str, Any]] = None


UpdateGroupBody = Annotated[
    Union[_OktaGroupUpdateBody, _RoleGroupUpdateBody, _AppGroupUpdateBody],
    Field(discriminator="type"),
]


# --- Group/Role membership editor payloads ----------------------------------

# Okta user / group ids are exactly 20 characters wide. Validating the
# length per-element rejects malformed ids at the request boundary instead
# of letting them leak into the operation layer.
_OktaIdStr = Annotated[str, Field(min_length=20, max_length=20)]


class GroupMember(BaseModel):
    """Used by GET/PUT /api/groups/{id}/members.

    `members_should_expire` and `owners_should_expire` are lists of
    OktaUserGroupMember row ids (integers). The other lists are user ids
    (strings).
    """

    model_config = ConfigDict(extra="ignore")
    members: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    members_to_add: list[_OktaIdStr]
    members_to_remove: list[_OktaIdStr]
    owners_to_add: list[_OktaIdStr]
    owners_to_remove: list[_OktaIdStr]
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
    groups_to_add: list[_OktaIdStr]
    groups_to_remove: list[_OktaIdStr]
    owner_groups_to_add: list[_OktaIdStr]
    owner_groups_to_remove: list[_OktaIdStr]
    groups_should_expire: list[int] = Field(default_factory=list)
    owner_groups_should_expire: list[int] = Field(default_factory=list)
    groups_added_ending_at: RFC822DatetimeOpt = None
    created_reason: Optional[str] = ""
