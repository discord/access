"""Pydantic v2 models for the Access API.

Each Pydantic class is the canonical shape for a specific endpoint surface;
there is no per-call field projection.

Polymorphic groups are modelled as discriminated unions on the `type` field.
Two union shapes are exposed here:

  - `GroupDetail`  — full detail (used by `/api/groups/{id}`)
  - `GroupSummary` — compact list view (used by `/api/groups`)

Request-body unions for create/update live in `requests_schemas.py`.

To keep field counts manageable, deeply nested membership/role/tag
relationships are emitted via dedicated child schemas, not full polymorphic
re-entries.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.schemas.rfc822 import RFC822Datetime, RFC822DatetimeOpt


# --- Tags -------------------------------------------------------------------


class TagDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: RFC822Datetime
    updated_at: RFC822Datetime
    deleted_at: RFC822DatetimeOpt = None
    # Resolved post-class via model_rebuild() — OktaGroupTagMapDetail is defined
    # below.
    active_group_tags: list["OktaGroupTagMapDetail"] = Field(default_factory=list)


class TagSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class TagListItem(BaseModel):
    """Tag list-endpoint item. Slim field set (id, name, description,
    enabled, constraints, created_at, updated_at) — does not hydrate
    `active_group_tags`, which would be an N+1 across the page."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: RFC822Datetime
    updated_at: RFC822Datetime


class OktaGroupTagMapDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: RFC822Datetime
    ended_at: RFC822DatetimeOpt = None
    active_tag: Optional[TagSummary] = None
    # Populated when the row is reached from the Tag side (`tag.active_group_tags`):
    active_group: Optional["_GroupRefForMembership"] = None
    active_app_tag_mapping: Optional["AppTagMapDetail"] = None


class AppTagMapDetail(BaseModel):
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


class AppDetail(AppSummary):
    """Full App detail."""

    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    active_app_tags: list[AppTagMapDetail] = Field(default_factory=list)
    # Populated post-class-definition once `AppGroupDetail` exists (forward refs
    # resolved via `model_rebuild()` at the bottom of this file).
    active_owner_app_groups: list["AppGroupDetail"] = Field(default_factory=list)
    active_non_owner_app_groups: list["AppGroupDetail"] = Field(default_factory=list)


# --- Users ------------------------------------------------------------------


class OktaUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    # The list endpoint includes timestamps so the frontend can sort/group by
    # creation time. Flask exposed these via Marshmallow's `only=(...)` tuple;
    # leaving them off the Pydantic summary loses that capability.
    created_at: RFC822DatetimeOpt = None
    updated_at: RFC822DatetimeOpt = None
    deleted_at: RFC822DatetimeOpt = None


class OktaUserDetail(OktaUserSummary):
    profile: dict[str, Any] = Field(default_factory=dict)
    manager: Optional["OktaUserSummary"] = None
    # Membership / ownership lists. Resolved post-class via model_rebuild()
    # because OktaUserGroupMemberDetail is defined further down.
    all_group_memberships_and_ownerships: list["OktaUserGroupMemberDetail"] = Field(default_factory=list)
    active_group_memberships_and_ownerships: list["OktaUserGroupMemberDetail"] = Field(default_factory=list)
    active_group_memberships: list["OktaUserGroupMemberDetail"] = Field(default_factory=list)
    active_group_ownerships: list["OktaUserGroupMemberDetail"] = Field(default_factory=list)

    @field_validator("profile", mode="before")
    @classmethod
    def _filter_profile_attrs(cls, value: Any) -> dict[str, Any]:
        """Only emit profile keys present in `USER_DISPLAY_CUSTOM_ATTRIBUTES`."""
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


class OktaUserGroupMemberDetail(BaseModel):
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


class RoleGroupMapDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_owner: Optional[bool] = None
    created_at: RFC822Datetime
    ended_at: RFC822DatetimeOpt = None
    role_group: Optional[_RoleGroupRef] = None
    active_role_group: Optional[_RoleGroupRef] = None
    group: Optional[_GroupRefForMembership] = None
    active_group: Optional[_GroupRefForMembership] = None


# --- Polymorphic groups -----------------------------------------------------
# Two discriminated unions: detail (Out) and summary (list view). The
# request-body unions live in `requests_schemas.py`.


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
    active_user_memberships: list[OktaUserGroupMemberDetail] = Field(default_factory=list)
    active_user_ownerships: list[OktaUserGroupMemberDetail] = Field(default_factory=list)
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)


class OktaGroupDetail(_GroupBase):
    type: Literal["okta_group"] = "okta_group"
    active_role_member_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)
    active_role_owner_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)


class RoleGroupDetail(_GroupBase):
    type: Literal["role_group"] = "role_group"
    active_role_associated_group_member_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)
    active_role_associated_group_owner_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)


class AppGroupDetail(_GroupBase):
    type: Literal["app_group"] = "app_group"
    app_id: Optional[str] = None
    is_owner: bool = False
    plugin_data: Optional[dict[str, Any]] = None
    app: Optional[AppIdRef] = None
    active_role_member_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)
    active_role_owner_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)


GroupDetail = Annotated[
    Union[OktaGroupDetail, RoleGroupDetail, AppGroupDetail],
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
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)


class OktaGroupSummary(_GroupSummaryBase):
    type: Literal["okta_group"] = "okta_group"


class RoleGroupSummary(_GroupSummaryBase):
    type: Literal["role_group"] = "role_group"
    active_role_associated_group_member_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)
    active_role_associated_group_owner_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)


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


# Manage forward refs after all classes are defined
OktaUserDetail.model_rebuild()
AppDetail.model_rebuild()
TagDetail.model_rebuild()
OktaGroupTagMapDetail.model_rebuild()
