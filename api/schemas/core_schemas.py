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

from api.schemas.datetimes import FlexibleDatetime, FlexibleDatetimeOpt


# --- Tags -------------------------------------------------------------------


class TagDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    deleted_at: FlexibleDatetimeOpt = None
    # Resolved post-class via model_rebuild() — OktaGroupTagMapDetail is defined
    # below.
    active_group_tags: list["OktaGroupTagMapDetail"] = Field(default_factory=list)
    # Tag detail also lists the apps this tag is attached to. Flask
    # `TagResource.get()` `exclude=("all_group_tags", "all_app_tags")`
    # retains the `active_app_tags` projection.
    active_app_tags: list["AppTagMapDetail"] = Field(default_factory=list)


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
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime


class OktaGroupTagMapDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: FlexibleDatetime
    ended_at: FlexibleDatetimeOpt = None
    active_tag: Optional[TagSummary] = None
    # Populated when the row is reached from the Tag side (`tag.active_group_tags`):
    active_group: Optional["_GroupRefForMembership"] = None
    active_app_tag_mapping: Optional["AppTagMapDetail"] = None


class AppTagMapDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: FlexibleDatetime
    ended_at: FlexibleDatetimeOpt = None
    active_tag: Optional[TagSummary] = None
    # Populated when the row is reached from the Tag side
    # (`tag.active_app_tags`). Flask emitted `active_app.{id, name, description}`
    # via the legacy AppTagMapSchema only-list.
    active_app: Optional["AppSummary"] = None


# --- Apps -------------------------------------------------------------------


class AppIdRef(BaseModel):
    """Inline reference to an App by id (used in compact group views).

    Flask `AppGroupSchema.app = Nested(AppSchema, only=("id", "name",
    "deleted_at", "app_group_lifecycle_plugin"))` exposed the lifecycle
    plugin id on every embedded app reference so the React frontend can
    dispatch on plugin behaviour without a follow-up `/api/apps/{id}` fetch.
    """

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: Optional[str] = None
    deleted_at: FlexibleDatetimeOpt = None
    app_group_lifecycle_plugin: Optional[str] = None


class AppSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: Optional[str] = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    deleted_at: FlexibleDatetimeOpt = None


class AppDetail(AppSummary):
    """Full App detail."""

    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None
    active_app_tags: list[AppTagMapDetail] = Field(default_factory=list)
    # Flask `AppResource.get()` `DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS` strips
    # `active_role_member_mappings`, `active_role_owner_mappings`, and
    # `active_group_tags` on the *nested* app groups (those land via
    # `active_owner_app_groups.<dotted-path>` exclude entries). Use a
    # dedicated slimmer shape — `AppGroupForAppDetail` — instead of
    # `AppGroupDetail` so the App-detail response doesn't pull every
    # role-association mapping per nested group. Forward refs resolved
    # via `model_rebuild()` at the bottom of this file.
    active_owner_app_groups: list["AppGroupForAppDetail"] = Field(default_factory=list)
    active_non_owner_app_groups: list["AppGroupForAppDetail"] = Field(default_factory=list)


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
    created_at: FlexibleDatetimeOpt = None
    updated_at: FlexibleDatetimeOpt = None
    deleted_at: FlexibleDatetimeOpt = None


def _filter_profile_attrs(value: Any) -> dict[str, Any]:
    """Filter `OktaUser.profile` to keys in `USER_DISPLAY_CUSTOM_ATTRIBUTES`.

    Marshmallow applied the same filter via `OktaUserSchema.get_attribute`;
    the FastAPI side reuses this helper anywhere `profile` is exposed
    (top-level on `OktaUserDetail`, nested on `OktaUserManagerRef`)."""
    from api.config import settings

    attrs_to_display = [a for a in settings.USER_DISPLAY_CUSTOM_ATTRIBUTES.split(",") if a]
    if not attrs_to_display:
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in attrs_to_display}


class OktaUserManagerRef(OktaUserSummary):
    """Embedded manager reference inside `OktaUserDetail`.

    Flask's `OktaUserSchema.manager = Nested(OktaUserSchema, exclude=(...))`
    retained `profile` (filtered to `USER_DISPLAY_CUSTOM_ATTRIBUTES`). The
    React user-detail page reads `manager.profile.Title` to render the
    manager's job title alongside their name."""

    profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile", mode="before")
    @classmethod
    def _filter_profile(cls, value: Any) -> dict[str, Any]:
        return _filter_profile_attrs(value)


class OktaUserDetail(OktaUserSummary):
    profile: dict[str, Any] = Field(default_factory=dict)
    manager: Optional[OktaUserManagerRef] = None
    # Membership / ownership lists. The aggregated
    # `*_group_memberships_and_ownerships` pair Flask's `UserResource.get()`
    # excluded — they duplicate the data already in `active_group_memberships`
    # and `active_group_ownerships` and bloat the response. Resolved
    # post-class via model_rebuild() because OktaUserGroupMemberDetail is
    # defined further down.
    active_group_memberships: list["OktaUserGroupMemberDetail"] = Field(default_factory=list)
    active_group_ownerships: list["OktaUserGroupMemberDetail"] = Field(default_factory=list)

    @field_validator("profile", mode="before")
    @classmethod
    def _filter_profile(cls, value: Any) -> dict[str, Any]:
        return _filter_profile_attrs(value)


# --- Group memberships ------------------------------------------------------


class _GroupRefForMembership(BaseModel):
    """Compact group reference embedded in user/group membership rows."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    name: str
    description: Optional[str] = None
    is_owner: Optional[bool] = None
    is_managed: Optional[bool] = None
    deleted_at: FlexibleDatetimeOpt = None
    app: Optional[AppIdRef] = None


class _RoleGroupRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    name: str
    is_managed: Optional[bool] = None
    deleted_at: FlexibleDatetimeOpt = None


class _RoleGroupMappingForMembership(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: FlexibleDatetime
    ended_at: FlexibleDatetimeOpt = None
    active_role_group: Optional[_RoleGroupRef] = None
    role_group: Optional[_RoleGroupRef] = None


class OktaUserGroupMemberDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_owner: Optional[bool] = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetimeOpt = None
    ended_at: FlexibleDatetimeOpt = None
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
    created_at: FlexibleDatetime
    ended_at: FlexibleDatetimeOpt = None
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
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    deleted_at: FlexibleDatetimeOpt = None
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


class AppGroupForAppDetail(BaseModel):
    """Slimmer shape used inside `AppDetail.active_*_app_groups`.

    Flask's `AppResource.get()` excluded `active_role_member_mappings`,
    `active_role_owner_mappings`, and `active_group_tags` on the nested
    app-groups via dotted-path entries in `DEFAULT_SCHEMA_DISPLAY_EXCLUSIONS`.
    The outer `AppGroupDetail` retains them for direct
    `GET /api/groups/{id}` calls; this variant drops them when the same
    rows are embedded inside an `AppDetail` payload."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: Literal["app_group"] = "app_group"
    name: str
    description: Optional[str] = ""
    is_managed: bool = True
    externally_managed_data: Optional[dict[str, Any]] = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    deleted_at: FlexibleDatetimeOpt = None
    app_id: Optional[str] = None
    is_owner: bool = False
    plugin_data: Optional[dict[str, Any]] = None
    app: Optional[AppIdRef] = None
    active_user_memberships: list[OktaUserGroupMemberDetail] = Field(default_factory=list)
    active_user_ownerships: list[OktaUserGroupMemberDetail] = Field(default_factory=list)


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
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
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


class RoleGroupListItem(BaseModel):
    """Slim row shape for `GET /api/roles`.

    Flask `RoleList.get()` `only=(id, type, name, description, created_at,
    updated_at)`. The role-list page does not render tags or role
    associations, so we pay neither the loader cost nor the JSON bloat
    from emitting them on every row."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: Literal["role_group"] = "role_group"
    name: str
    description: Optional[str] = ""
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime


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
    deleted_at: FlexibleDatetimeOpt = None


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


# Wider polymorphic refs surfaced inside `RoleRequestDetail`. The role-request
# detail page renders the role's current members and the target group's tags
# inline, so those two relationship arrays must travel with the embedded
# group refs (the bare `GroupRef` shape intentionally omits them to keep
# audit/list payloads slim).
class RoleRequestRequesterRoleRef(_GroupRefBase):
    type: Literal["role_group"] = "role_group"
    active_user_memberships: list[OktaUserGroupMemberDetail] = Field(default_factory=list)


class RoleRequestRequestedOktaGroupRef(_GroupRefBase):
    type: Literal["okta_group"] = "okta_group"
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)


class RoleRequestRequestedAppGroupRef(_GroupRefBase):
    type: Literal["app_group"] = "app_group"
    app_id: Optional[str] = None
    is_owner: bool = False
    app: Optional[AppIdRef] = None
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)


RoleRequestRequestedGroupRef = Annotated[
    Union[RoleRequestRequestedOktaGroupRef, RoleRequestRequestedAppGroupRef],
    Field(discriminator="type"),
]


# --- Members views (list-of-IDs response shapes) ----------------------------


class GroupMembersSummary(BaseModel):
    """Wire shape for `GET/PUT /api/groups/{id}/members`."""

    members: list[str]
    owners: list[str]


class RoleMembersSummary(BaseModel):
    """Wire shape for `GET/PUT /api/roles/{id}/members`."""

    groups_in_role: list[str]
    groups_owned_by_role: list[str]


# --- Error envelope ---------------------------------------------------------


class ErrorMessage(BaseModel):
    """Wire shape emitted by `api/exception_handlers.py`. Declared here so
    routes can advertise it via `responses={...}` for OpenAPI codegen."""

    message: str


# Manage forward refs after all classes are defined
OktaUserDetail.model_rebuild()
AppDetail.model_rebuild()
TagDetail.model_rebuild()
OktaGroupTagMapDetail.model_rebuild()
AppTagMapDetail.model_rebuild()
