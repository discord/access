"""Pydantic v2 models for the Access API.

These replace the Marshmallow schemas under `api/views/schemas/core_schemas.py`.
Each Pydantic class is the canonical shape for a specific endpoint surface;
there is no per-call `only=`/`exclude=` projection.

Polymorphic groups are modelled as discriminated unions on the `type` field.
Three union shapes are exposed:

  - `GroupOut`     — full detail (used by `/api/groups/{id}`)
  - `GroupSummary` — compact list view (used by `/api/groups`)
  - `GroupIn`      — request body for create/update

To keep field counts manageable, deeply nested membership/role/tag
relationships are emitted via dedicated child schemas, not full polymorphic
re-entries.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.access_config import get_access_config
from api.config import settings
from api.schemas.rfc822 import RFC822Datetime, RFC822DatetimeOpt

_access_config = get_access_config()
_NAME_PATTERN = re.compile(_access_config.name_pattern)


def _validate_name(value: str) -> str:
    if not _NAME_PATTERN.match(value):
        raise ValueError(f"Group {_access_config.name_validation_error}")
    if not (1 <= len(value) <= 255):
        raise ValueError("Name must be between 1 and 255 characters")
    return value


# --- Tags -------------------------------------------------------------------


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    deleted_at: RFC822DatetimeOpt = None


class TagSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class TagIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    constraints: Optional[dict[str, Any]] = None
    enabled: bool = True


class OktaGroupTagMapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: RFC822Datetime
    ended_at: RFC822DatetimeOpt = None
    active_tag: Optional[TagSummary] = None


class AppTagMapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: RFC822Datetime
    ended_at: RFC822DatetimeOpt = None
    active_tag: Optional[TagSummary] = None


# --- Apps -------------------------------------------------------------------


class AppIdRef(BaseModel):
    """Inline reference to an App by id (used in compact group views)."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: Optional[str] = None
    deleted_at: RFC822DatetimeOpt = None


class AppSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    deleted_at: RFC822DatetimeOpt = None


class AppOut(AppSummary):
    """Full App detail."""
    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    active_app_tags: list[AppTagMapOut] = Field(default_factory=list)
    # Populated post-class-definition once `AppGroupOut` exists (forward refs
    # resolved via `model_rebuild()` at the bottom of this file).
    active_owner_app_groups: list["AppGroupOut"] = Field(default_factory=list)
    active_non_owner_app_groups: list["AppGroupOut"] = Field(default_factory=list)


class AppIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    initial_owner_id: Optional[str] = None
    initial_owner_role_ids: Optional[list[str]] = None
    initial_additional_app_groups: Optional[list[dict[str, Any]]] = None
    tags_to_add: list[str] = Field(default_factory=list)


# --- Users ------------------------------------------------------------------


class OktaUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    deleted_at: RFC822DatetimeOpt = None


class OktaUserOut(OktaUserSummary):
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    profile: dict[str, Any] = Field(default_factory=dict)
    manager: Optional["OktaUserSummary"] = None
    # Membership / ownership lists. Resolved post-class via model_rebuild()
    # because OktaUserGroupMemberOut is defined further down.
    all_group_memberships_and_ownerships: list["OktaUserGroupMemberOut"] = Field(default_factory=list)
    active_group_memberships_and_ownerships: list["OktaUserGroupMemberOut"] = Field(default_factory=list)
    active_group_memberships: list["OktaUserGroupMemberOut"] = Field(default_factory=list)
    active_group_ownerships: list["OktaUserGroupMemberOut"] = Field(default_factory=list)

    @field_validator("profile", mode="before")
    @classmethod
    def _filter_profile_attrs(cls, value: Any) -> dict[str, Any]:
        """Mirror the Marshmallow `OktaUserSchema.get_attribute` filter:
        only emit profile keys present in `USER_DISPLAY_CUSTOM_ATTRIBUTES`.
        """
        from api.config import settings

        attrs_to_display = [a for a in settings.USER_DISPLAY_CUSTOM_ATTRIBUTES.split(",") if a]
        if not attrs_to_display:
            return {}
        if not isinstance(value, dict):
            return {}
        return {key: value.get(key) for key in attrs_to_display}


# --- Group memberships ------------------------------------------------------


class _GroupRefForMembership(BaseModel):
    """Compact group reference embedded in user/group membership rows."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    name: str
    is_owner: Optional[bool] = None
    is_managed: Optional[bool] = None
    deleted_at: RFC822DatetimeOpt = None
    app: Optional[AppIdRef] = None


class _RoleGroupRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    name: str
    is_managed: Optional[bool] = None
    deleted_at: RFC822DatetimeOpt = None


class _RoleGroupMappingForMembership(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: RFC822Datetime
    ended_at: RFC822DatetimeOpt = None
    active_role_group: Optional[_RoleGroupRef] = None
    role_group: Optional[_RoleGroupRef] = None


class OktaUserGroupMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_owner: Optional[bool] = None
    created_at: RFC822Datetime
    updated_at: Optional[RFC822DatetimeOpt] = None
    ended_at: RFC822DatetimeOpt = None
    created_reason: Optional[str] = ""
    should_expire: Optional[bool] = None
    user: Optional[OktaUserSummary] = None
    active_user: Optional[OktaUserSummary] = None
    group: Optional[_GroupRefForMembership] = None
    active_group: Optional[_GroupRefForMembership] = None
    role_group_mapping: Optional[_RoleGroupMappingForMembership] = None
    active_role_group_mapping: Optional[_RoleGroupMappingForMembership] = None


# --- Role group mappings ----------------------------------------------------


class RoleGroupMapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_owner: Optional[bool] = None
    created_at: RFC822Datetime
    ended_at: RFC822DatetimeOpt = None
    role_group: Optional[_RoleGroupRef] = None
    active_role_group: Optional[_RoleGroupRef] = None
    group: Optional[_GroupRefForMembership] = None
    active_group: Optional[_GroupRefForMembership] = None


# --- Polymorphic groups -----------------------------------------------------
# Three discriminated unions: detail (Out), summary (list view), and input.


class _GroupBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = ""
    is_managed: bool = True
    externally_managed_data: Optional[dict[str, Any]] = None
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    deleted_at: RFC822DatetimeOpt = None
    active_user_memberships: list[OktaUserGroupMemberOut] = Field(default_factory=list)
    active_user_ownerships: list[OktaUserGroupMemberOut] = Field(default_factory=list)
    active_group_tags: list[OktaGroupTagMapOut] = Field(default_factory=list)


class OktaGroupOut(_GroupBase):
    type: Literal["okta_group"] = "okta_group"
    active_role_member_mappings: list[RoleGroupMapOut] = Field(default_factory=list)
    active_role_owner_mappings: list[RoleGroupMapOut] = Field(default_factory=list)


class RoleGroupOut(_GroupBase):
    type: Literal["role_group"] = "role_group"
    active_role_associated_group_member_mappings: list[RoleGroupMapOut] = Field(default_factory=list)
    active_role_associated_group_owner_mappings: list[RoleGroupMapOut] = Field(default_factory=list)


class AppGroupOut(_GroupBase):
    type: Literal["app_group"] = "app_group"
    app_id: Optional[str] = None
    is_owner: bool = False
    plugin_data: Optional[dict[str, Any]] = None
    app: Optional[AppIdRef] = None
    active_role_member_mappings: list[RoleGroupMapOut] = Field(default_factory=list)
    active_role_owner_mappings: list[RoleGroupMapOut] = Field(default_factory=list)


GroupOut = Annotated[
    Union[OktaGroupOut, RoleGroupOut, AppGroupOut],
    Field(discriminator="type"),
]


# --- Group summaries (list endpoints) --------------------------------------


class _GroupSummaryBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = ""
    is_managed: bool = True
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    active_group_tags: list[OktaGroupTagMapOut] = Field(default_factory=list)


class OktaGroupSummary(_GroupSummaryBase):
    type: Literal["okta_group"] = "okta_group"


class RoleGroupSummary(_GroupSummaryBase):
    type: Literal["role_group"] = "role_group"
    active_role_associated_group_member_mappings: list[RoleGroupMapOut] = Field(default_factory=list)
    active_role_associated_group_owner_mappings: list[RoleGroupMapOut] = Field(default_factory=list)


class AppGroupSummary(_GroupSummaryBase):
    type: Literal["app_group"] = "app_group"
    app: Optional[AppIdRef] = None


GroupSummary = Annotated[
    Union[OktaGroupSummary, RoleGroupSummary, AppGroupSummary],
    Field(discriminator="type"),
]


# --- Group references (embedded inside requests, audit, etc.) -------------
# A minimal polymorphic group shape that does NOT touch any "active_*" or
# "all_*" relationship attributes — those are `lazy="raise_on_sql"` and not
# always pre-loaded when groups are embedded inside other objects.


class _GroupRefBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = ""
    is_managed: bool = True
    deleted_at: RFC822DatetimeOpt = None


class OktaGroupRef(_GroupRefBase):
    type: Literal["okta_group"] = "okta_group"


class RoleGroupRef(_GroupRefBase):
    type: Literal["role_group"] = "role_group"


class AppGroupRef(_GroupRefBase):
    type: Literal["app_group"] = "app_group"
    app_id: Optional[str] = None
    is_owner: bool = False
    app: Optional[AppIdRef] = None


GroupRef = Annotated[
    Union[OktaGroupRef, RoleGroupRef, AppGroupRef],
    Field(discriminator="type"),
]


# --- Group inputs (create/update) ------------------------------------------


def _validate_description(value: Optional[str]) -> str:
    if value is None:
        return ""
    if settings.REQUIRE_DESCRIPTIONS:
        if value == "":
            raise ValueError("Description is required.")
        if not (1 <= len(value) <= 1024):
            raise ValueError("Description must be between 1 and 1024 characters")
    elif len(value) > 1024:
        raise ValueError("Description must be 1024 characters or less")
    return value


class _GroupInBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: Optional[str] = None
    tags_to_add: list[str] = Field(default_factory=list)
    tags_to_remove: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v: Optional[str]) -> str:
        return _validate_description(v)


class OktaGroupIn(_GroupInBase):
    type: Literal["okta_group"]


class RoleGroupIn(_GroupInBase):
    type: Literal["role_group"]


class AppGroupIn(_GroupInBase):
    type: Literal["app_group"]
    app_id: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None


GroupIn = Annotated[
    Union[OktaGroupIn, RoleGroupIn, AppGroupIn],
    Field(discriminator="type"),
]


# Manage forward refs after all classes are defined
OktaUserOut.model_rebuild()
AppOut.model_rebuild()
