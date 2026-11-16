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

from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from api.access_config import get_access_config
from api.config import settings
from api.schemas.core_schemas import (
    AppIdRef,
    GroupRef,
    OktaGroupTagMapDetail,
    OktaUserSummary,
    RoleGroupMapDetail,
    RoleRequestRequestedGroupRef,
    RoleRequestRequesterRoleRef,
)
from api.schemas.datetimes import FlexibleDatetime

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


def _strip_reason(value: Any) -> Any:
    """Normalize inbound justification text.

    Whether a reason was given is decided by `CheckForReason` on the stripped
    value, so stripping on the way in is what makes the gate and the store
    agree by construction: a reason that reads as blank is treated as absent
    everywhere, rather than as absent by a tagged group and present by an
    untagged one. The same holds for `_require_request_reason`, which reads
    the stripped value too.

    Non-string input passes through for pydantic to reject or coerce, so this
    adds no type handling of its own.
    """
    return value.strip() if isinstance(value, str) else value


def _require_request_reason(value: str | None) -> str | None:
    """Reject a missing reason on a new request when `REQUIRE_REQUEST_REASONS` is set."""
    if settings.REQUIRE_REQUEST_REASONS and not value:
        raise ValueError("A reason is required.")
    return value


#: An inbound justification field. Whitespace-only collapses to `""`, which the
#: reason constraints already treat as not provided. Response models keep plain
#: `str | None`: they report what is stored, and normalizing on the way out
#: would hide stored values that were never normalized.
ReasonStr = Annotated[str | None, BeforeValidator(_strip_reason)]

#: The requester's justification on a new access, role, or group request. It is
#: required when the operator sets `REQUIRE_REQUEST_REASONS`, and validated even
#: when omitted so that an absent field is rejected as well as an empty one.
#: Checking it here, in the body, also keeps a rejected request from first
#: superseding the requester's earlier pending one.
RequestReasonStr = Annotated[
    str | None,
    BeforeValidator(_strip_reason),
    AfterValidator(_require_request_reason),
    Field(validate_default=True),
]


# Discriminated-union refs for the *detail* `requested_group`. Each variant
# matches the per-type Flask `only=` whitelist for `AccessRequestResource.get()`:
# - OktaGroup: id/type/name/deleted_at + active_group_tags
# - RoleGroup: id/type/name/deleted_at + active_group_tags +
#   active_role_associated_group_member_mappings + active_role_associated_group_owner_mappings
# - AppGroup: id/type/name/deleted_at + is_owner + app + active_group_tags
class _AccessRequestOktaGroupRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: Literal["okta_group"] = "okta_group"
    name: str
    deleted_at: FlexibleDatetime | None = None
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)


class _AccessRequestRoleGroupRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: Literal["role_group"] = "role_group"
    name: str
    deleted_at: FlexibleDatetime | None = None
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)
    active_role_associated_group_member_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)
    active_role_associated_group_owner_mappings: list[RoleGroupMapDetail] = Field(default_factory=list)


class _AccessRequestAppGroupRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: Literal["app_group"] = "app_group"
    name: str
    deleted_at: FlexibleDatetime | None = None
    is_owner: bool = False
    app: AppIdRef | None = None
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)


_RichRequestedGroupRef = Annotated[
    _AccessRequestOktaGroupRef | _AccessRequestRoleGroupRef | _AccessRequestAppGroupRef,
    Field(discriminator="type"),
]


class AccessRequestDetail(BaseModel):
    """Response for `GET /api/requests/{id}` only.

    Retains a richer `requested_group` projection (group tags +
    role-association mappings on role groups, plus `app`/`is_owner` on app
    groups). We split the detail shape from the list/POST/PUT shape
    (`AccessRequestSummary`) because SQLAlchemy `lazy="raise_on_sql"` won't let
    one Pydantic model conditionally include the relationships at runtime — the
    eager-loads differ per endpoint."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    requester_user_id: str | None = None
    requested_group_id: str | None = None
    request_ownership: bool = False
    request_reason: str | None = ""
    request_ending_at: FlexibleDatetime | None = None
    status: str
    resolver_user_id: str | None = None
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    approval_ending_at: FlexibleDatetime | None = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    requester: OktaUserSummary | None = None
    active_requester: OktaUserSummary | None = None
    requested_group: _RichRequestedGroupRef | None = None
    active_requested_group: GroupRef | None = None
    resolver: OktaUserSummary | None = None
    active_resolver: OktaUserSummary | None = None


class AccessRequestSummary(BaseModel):
    """Response for `GET /api/requests`, `POST /api/requests`, and
    `PUT /api/requests/{id}`. Scalar fields the frontend reads
    (resolution_reason, request_reason, etc.) are emitted as `null` when
    absent. The `requested_group` is the lightweight `GroupRef` — no group
    tags or role-association mappings, which only the detail page renders."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    requester_user_id: str | None = None
    requested_group_id: str | None = None
    request_ownership: bool = False
    request_reason: str | None = ""
    request_ending_at: FlexibleDatetime | None = None
    status: str
    resolver_user_id: str | None = None
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    approval_ending_at: FlexibleDatetime | None = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    requester: OktaUserSummary | None = None
    active_requester: OktaUserSummary | None = None
    requested_group: GroupRef | None = None
    active_requested_group: GroupRef | None = None
    resolver: OktaUserSummary | None = None
    active_resolver: OktaUserSummary | None = None


class CreateAccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    group_id: str
    group_owner: bool = False
    reason: RequestReasonStr = ""
    ending_at: FlexibleDatetime | None = None


class ResolveAccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # `StrictBool` rejects `"false"` / `"true"` strings outright. The
    # legacy Marshmallow schema declared `fields.Boolean(required=True)`
    # which had the same behaviour; Python's `bool()` (which the migration
    # briefly used) interprets every non-empty string as True and silently
    # turns "false" into an APPROVED outcome.
    approved: StrictBool
    reason: ReasonStr = ""
    ending_at: FlexibleDatetime | None = None


# --- Role requests ----------------------------------------------------------


class RoleRequestDetail(BaseModel):
    """Response for `GET /api/role-requests/{id}` only.

    Carries the rich `requester_role` / `requested_group` projections —
    `requester_role.active_user_memberships` and the `requested_group`
    `active_group_tags` (plus `is_owner` + `app` for app-group requested
    groups) — that the React role-request detail page renders. List /
    POST / PUT use `RoleRequestSummary` instead because their eager-load
    chain is slimmer."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    requester_user_id: str | None = None
    requester_role_id: str | None = None
    requested_group_id: str | None = None
    request_ownership: bool = False
    request_reason: str | None = ""
    request_ending_at: FlexibleDatetime | None = None
    status: str
    resolver_user_id: str | None = None
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    approval_ending_at: FlexibleDatetime | None = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    requester: OktaUserSummary | None = None
    requester_role: RoleRequestRequesterRoleRef | None = None
    # `active_requester_role` and `active_requested_group` use the lenient
    # polymorphic `GroupRef` because `active_requester_role`'s SQLAlchemy
    # primaryjoin currently mirrors `active_requested_group` (joins on
    # `requested_group_id`), so the value at runtime is the requested group,
    # not the requester role. Tightening the type to a role-only literal
    # would surface that model-side defect as a 500.
    active_requester_role: GroupRef | None = None
    requested_group: RoleRequestRequestedGroupRef | None = None
    active_requested_group: GroupRef | None = None
    resolver: OktaUserSummary | None = None
    active_resolver: OktaUserSummary | None = None


class RoleRequestSummary(BaseModel):
    """Response for `GET /api/role-requests`, `POST /api/role-requests`,
    and `PUT /api/role-requests/{id}`. Slim shape — no role-member /
    group-tag eager-load on the embedded refs. The list / POST / PUT
    pages don't render that data inline."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    requester_user_id: str | None = None
    requester_role_id: str | None = None
    requested_group_id: str | None = None
    request_ownership: bool = False
    request_reason: str | None = ""
    request_ending_at: FlexibleDatetime | None = None
    status: str
    resolver_user_id: str | None = None
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    approval_ending_at: FlexibleDatetime | None = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    requester: OktaUserSummary | None = None
    requester_role: GroupRef | None = None
    active_requester_role: GroupRef | None = None
    requested_group: GroupRef | None = None
    active_requested_group: GroupRef | None = None
    resolver: OktaUserSummary | None = None
    active_resolver: OktaUserSummary | None = None


class CreateRoleRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role_id: str
    group_id: str
    group_owner: bool = False
    reason: RequestReasonStr = ""
    ending_at: FlexibleDatetime | None = None


class ResolveRoleRequestBody(ResolveAccessRequestBody):
    pass


# --- Group requests ---------------------------------------------------------


class GroupRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    # Column names use `requested_*` and `resolved_*` to mirror the underlying
    # ORM fields the React frontend's apiSchemas already consume.
    requested_group_name: str | None = None
    requested_group_description: str | None = ""
    requested_group_type: str | None = None
    requested_app_id: str | None = None
    requested_group_tags: list[str] = Field(default_factory=list)
    requested_ownership_ending_at: FlexibleDatetime | None = None
    request_reason: str | None = ""
    resolved_group_name: str | None = ""
    resolved_group_description: str | None = ""
    resolved_group_type: str | None = ""
    resolved_app_id: str | None = None
    resolved_group_tags: list[str] = Field(default_factory=list)
    resolved_ownership_ending_at: FlexibleDatetime | None = None
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    approved_group_id: str | None = None
    requested_plugin_data: dict[str, Any] = Field(default_factory=dict)
    resolved_plugin_data: dict[str, Any] = Field(default_factory=dict)
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    requester: OktaUserSummary | None = None
    resolver: OktaUserSummary | None = None

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
    requested_group_description: str | None = Field(default="", max_length=_GROUP_DESC_MAX_LENGTH)
    requested_group_tags: list[str] = Field(default_factory=list)
    requested_ownership_ending_at: FlexibleDatetime | None = None
    request_reason: RequestReasonStr = ""

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
    requested_plugin_data: dict[str, Any] = Field(default_factory=dict)


CreateGroupRequestBody = Annotated[
    _OktaGroupRequestBody | _RoleGroupRequestBody | _AppGroupRequestBody,
    Field(discriminator="requested_group_type"),
]


class ResolveGroupRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approved: StrictBool
    reason: ReasonStr = ""
    resolved_group_name: str | None = None
    resolved_group_description: str | None = None
    resolved_group_type: str | None = None
    resolved_app_id: str | None = None
    resolved_group_tags: list[str] | None = None
    resolved_ownership_ending_at: FlexibleDatetime | None = None
    resolved_plugin_data: dict[str, Any] | None = None


# --- Tags -------------------------------------------------------------------


def _validate_tag_constraints(v: dict[str, Any] | None) -> dict[str, Any] | None:
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


#: Ceiling on how many ids one request may name. The eager loads scale with
#: the requested set, so it is bounded rather than left to the caller. Well
#: above any real dialog selection.
_EFFECTIVE_CONSTRAINTS_MAX_IDS = 200


class EffectiveConstraintsQuery(BaseModel):
    """Query for GET /api/constraints/effective.

    Exactly one of the two lists is populated; the router rejects naming both
    or neither, since the two modes answer different questions and combining
    them would silently invent a third.
    """

    model_config = ConfigDict(extra="ignore")
    group_ids: list[str] = Field(default_factory=list, max_length=_EFFECTIVE_CONSTRAINTS_MAX_IDS)
    tag_ids: list[str] = Field(default_factory=list, max_length=_EFFECTIVE_CONSTRAINTS_MAX_IDS)


class CreateTagBody(BaseModel):
    """Body for POST /api/tags."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=_TAG_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=_TAG_DESC_MAX_LENGTH)
    constraints: dict[str, Any] | None = None
    enabled: bool = True
    propagate_to_roles: bool | None = None

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if settings.REQUIRE_DESCRIPTIONS and (self.description is None or self.description == ""):
            raise ValueError("Description is required.")
        return self

    @field_validator("constraints")
    @classmethod
    def _check_constraints(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_tag_constraints(v)


def _hide_default(schema: dict[str, Any]) -> None:
    """Drop `default` from a field's published schema.

    A partial-update field needs *a* default so it may be omitted, but the
    value is never used: the handler dumps with `exclude_unset=True`, so an
    omitted field is absent from the payload rather than set to its default.
    Publishing one would tell a client that omitting the field writes that
    value, which is the opposite of what happens.
    """
    schema.pop("default", None)


class UpdateTagBody(BaseModel):
    """Body for PUT /api/tags/{id}. All fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    # `name`, `enabled` and `propagate_to_roles` are deliberately not
    # `Optional`: their columns forbid null and none of them has a meaningful
    # empty value, so the annotation rejects an explicit `null` on its own. The
    # defaults exist only to keep the fields omittable for a partial update and
    # are never applied, hence `_hide_default`. `description` and `constraints`
    # stay nullable because a null there *is* meaningful -- the handler coerces
    # it to `""` / `{}`.
    name: str = Field(default="", min_length=1, max_length=_TAG_NAME_MAX_LENGTH, json_schema_extra=_hide_default)
    description: str | None = Field(default=None, max_length=_TAG_DESC_MAX_LENGTH)
    constraints: dict[str, Any] | None = None
    enabled: bool = Field(default=True, json_schema_extra=_hide_default)
    propagate_to_roles: bool = Field(default=True, json_schema_extra=_hide_default)

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
    def _check_constraints(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_tag_constraints(v)


# --- Apps -------------------------------------------------------------------


class _InitialAppGroupBody(BaseModel):
    """Body shape for an entry in `initial_additional_app_groups`."""

    model_config = ConfigDict(extra="ignore")
    name: str
    description: str | None = ""
    type: str | None = "app_group"
    plugin_data: dict[str, Any] | None = None
    tags_to_add: list[str] | None = None


class CreateAppBody(BaseModel):
    """Body for POST /api/apps."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=_APP_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=_APP_DESC_MAX_LENGTH)
    initial_owner_id: str | None = None
    initial_owner_role_ids: list[str] | None = None
    initial_additional_app_groups: list[_InitialAppGroupBody] | None = None
    tags_to_add: list[str] | None = None
    app_group_lifecycle_plugin: str | None = None
    plugin_data: dict[str, Any] | None = None

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
    name: str | None = Field(default=None, min_length=1, max_length=_APP_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=_APP_DESC_MAX_LENGTH)
    app_group_lifecycle_plugin: str | None = None
    plugin_data: dict[str, Any] | None = None
    tags_to_add: list[str] | None = None
    tags_to_remove: list[str] | None = None

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
    description: str | None = Field(default=None, max_length=_GROUP_DESC_MAX_LENGTH)
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
    app_id: str | None = None
    plugin_data: dict[str, Any] | None = None


CreateGroupBody = Annotated[
    _OktaGroupCreateBody | _RoleGroupCreateBody | _AppGroupCreateBody,
    Field(discriminator="type"),
]


class _GroupUpdateBodyShared(BaseModel):
    """Non-discriminator fields shared across the three update variants. All
    fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(
        default=None, pattern=_GROUP_NAME_PATTERN_STR, min_length=1, max_length=_GROUP_NAME_MAX_LENGTH
    )
    description: str | None = Field(default=None, max_length=_GROUP_DESC_MAX_LENGTH)
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
    app_id: str | None = None
    plugin_data: dict[str, Any] | None = None


UpdateGroupBody = Annotated[
    _OktaGroupUpdateBody | _RoleGroupUpdateBody | _AppGroupUpdateBody,
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
    users_added_ending_at: FlexibleDatetime | None = None
    created_reason: ReasonStr = ""


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
    groups_added_ending_at: FlexibleDatetime | None = None
    created_reason: ReasonStr = ""
