"""Audit log shaping.

Operations emit JSON-line audit logs via `AuditLogSchema().dumps(payload)`.
Each payload is a dict of (mostly) ORM instances; the schema below projects
each value to a tightly-scoped wire shape sourced from the legacy
Marshmallow `AuditLogSchema` (`fields.Nested(..., only=(...))`). Unknown
keys raise — the previous `__dict__` fallback was leaking the OktaUser
`profile` JSONB blob (HRIS data) into audit logs.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(Enum):
    access_approve = "ACCESS_APPROVE"
    access_create = "ACCESS_CREATE"
    access_reject = "ACCESS_REJECT"
    app_create = "APP_CREATE"
    app_delete = "APP_DELETE"
    app_modify_name = "APP_MODIFY_NAME"
    app_modify_plugin = "APP_MODIFY_PLUGIN"
    app_modify_tags = "APP_MODIFY_TAG"
    group_create = "GROUP_CREATE"
    group_delete = "GROUP_DELETE"
    group_modify_name = "GROUP_MODIFY_NAME"
    group_modify_plugin = "GROUP_MODIFY_PLUGIN"
    group_modify_type = "GROUP_MODIFY_TYPE"
    group_modify_tags = "GROUP_MODIFY_TAG"
    group_modify_users = "GROUP_MODIFY_USER"
    group_request_approve = "GROUP_REQUEST_APPROVE"
    group_request_create = "GROUP_REQUEST_CREATE"
    group_request_reject = "GROUP_REQUEST_REJECT"
    role_group_modify = "ROLE_GROUP_MODIFY"
    role_request_approve = "ROLE_REQUEST_APPROVE"
    role_request_create = "ROLE_REQUEST_CREATE"
    role_request_reject = "ROLE_REQUEST_REJECT"
    tag_create = "TAG_CREATE"
    tag_modify = "TAG_MODIFY"
    tag_delete = "TAG_DELETE"


# Keys whose None/empty value should be retained verbatim in the output.
# Matches the legacy Marshmallow `pre_dump` allowlist.
_ALLOWED_NULL_KEYS = frozenset(
    {
        "groups_added_ending_at",
        "current_user_id",
        "current_user_email",
        "old_app_group_lifecycle_plugin",
    }
)


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _project(obj: Any, *keys: str) -> Optional[dict[str, Any]]:
    """Build a `{key: getattr(obj, key) ...}` projection. Returns None when
    `obj` is None so dumping skips the field per `_ALLOWED_NULL_KEYS`."""
    if obj is None:
        return None
    return {k: _attr(obj, k) for k in keys}


# ---------------------------------------------------------------------------
# Projection models — one per audit-log nested shape. Field allowlists match
# the legacy `fields.Nested(..., only=(...))` declarations exactly.
# ---------------------------------------------------------------------------


class _AuditUserRef(BaseModel):
    """OktaUser projection for `requester` / `owners_*` / `members_*` / `group_owners`."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    email: Optional[str] = None


class _AuditAppRef(BaseModel):
    """App projection for the `app` audit field."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None
    app_group_lifecycle_plugin: Optional[str] = None
    plugin_data: Optional[dict[str, Any]] = None


class _AuditGroupAppRef(BaseModel):
    """Nested `app` shape inside `_AuditGroupRef` — only `id` and `name`."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None


class _AuditGroupRef(BaseModel):
    """PolymorphicGroup projection for `group` / `active_group`."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    app: Optional[_AuditGroupAppRef] = None
    plugin_data: Optional[dict[str, Any]] = None

    @field_validator("app", mode="before")
    @classmethod
    def _coerce_app(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        return _project(v, "id", "name")


class _AuditRoleRef(BaseModel):
    """RoleGroup projection for `role` / `groups_added_ids_names` / etc."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None


class _AuditUserGroupMemberRef(BaseModel):
    """OktaUserGroupMember projection for the `*_should_expire_user_id_group_id` lists."""

    model_config = ConfigDict(extra="ignore")
    user_id: Optional[str] = None
    group_id: Optional[str] = None


class _AuditRoleGroupMapRef(BaseModel):
    """RoleGroupMap projection for the `*_should_expire_role_id_group_id` lists."""

    model_config = ConfigDict(extra="ignore")
    role_group_id: Optional[str] = None
    group_id: Optional[str] = None


class _AuditRequesterRoleRef(BaseModel):
    """Inner `requester_role` shape on `role_request`."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None


class _AuditAccessRequestRef(BaseModel):
    """AccessRequest projection for the `request` field."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    request_reason: Optional[str] = None
    request_ending_at: Optional[datetime] = None
    request_ownership: Optional[bool] = None
    resolution_reason: Optional[str] = None
    approval_ending_at: Optional[datetime] = None


class _AuditRoleRequestRef(BaseModel):
    """RoleRequest projection for the `role_request` field."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    requester_role: Optional[_AuditRequesterRoleRef] = None
    request_reason: Optional[str] = None
    request_ending_at: Optional[datetime] = None
    request_ownership: Optional[bool] = None
    resolution_reason: Optional[str] = None
    approval_ending_at: Optional[datetime] = None

    @field_validator("requester_role", mode="before")
    @classmethod
    def _coerce_requester_role(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        return _project(v, "id", "name")


class _AuditGroupRequestRef(BaseModel):
    """GroupRequest projection for the `group_request` field."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    requested_group_name: Optional[str] = None
    requested_group_type: Optional[str] = None
    requested_app_id: Optional[str] = None
    requested_ownership_ending_at: Optional[datetime] = None
    request_reason: Optional[str] = None
    resolved_ownership_ending_at: Optional[datetime] = None
    resolution_reason: Optional[str] = None


class _AuditTagRef(BaseModel):
    """Tag projection for `tag` / `tags_added` / `tags_removed`."""

    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None
    constraints: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None


class _AuditOldTagRef(BaseModel):
    """Tag projection for `old_tag` (no `id`)."""

    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    constraints: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Coercion helpers — turn ORM instances into the projection models above so
# Pydantic's validators don't have to know about SQLAlchemy.
# ---------------------------------------------------------------------------


def _as_user_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "id", "email")


def _as_user_ref_list(v: Any) -> Any:
    if v is None:
        return None
    return [_as_user_ref(x) for x in v]


def _as_user_group_member_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "user_id", "group_id")


def _as_user_group_member_ref_list(v: Any) -> Any:
    if v is None:
        return None
    return [_as_user_group_member_ref(x) for x in v]


def _as_role_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "id", "name")


def _as_role_ref_list(v: Any) -> Any:
    if v is None:
        return None
    return [_as_role_ref(x) for x in v]


def _as_role_group_map_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "role_group_id", "group_id")


def _as_role_group_map_ref_list(v: Any) -> Any:
    if v is None:
        return None
    return [_as_role_group_map_ref(x) for x in v]


def _as_group_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "id", "name", "type", "app", "plugin_data")


def _as_app_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "id", "name", "app_group_lifecycle_plugin", "plugin_data")


def _as_access_request_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(
        v,
        "id",
        "request_reason",
        "request_ending_at",
        "request_ownership",
        "resolution_reason",
        "approval_ending_at",
    )


def _as_role_request_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(
        v,
        "id",
        "requester_role",
        "request_reason",
        "request_ending_at",
        "request_ownership",
        "resolution_reason",
        "approval_ending_at",
    )


def _as_group_request_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(
        v,
        "id",
        "requested_group_name",
        "requested_group_type",
        "requested_app_id",
        "requested_ownership_ending_at",
        "request_reason",
        "resolved_ownership_ending_at",
        "resolution_reason",
    )


def _as_tag_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "id", "name", "constraints", "enabled")


def _as_tag_ref_list(v: Any) -> Any:
    if v is None:
        return None
    return [_as_tag_ref(x) for x in v]


def _as_old_tag_ref(v: Any) -> Any:
    if v is None or isinstance(v, dict):
        return v
    return _project(v, "name", "constraints", "enabled")


# ---------------------------------------------------------------------------
# Top-level audit payload
# ---------------------------------------------------------------------------


class AuditLogPayload(BaseModel):
    """Pydantic representation of an audit-log entry. Every field is
    optional; the operations layer fills the subset relevant to the event
    type. `extra="forbid"` is the safety net that closes the previous
    `__dict__` fallback (audit-report HIGH-2)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    event_type: Optional[EventType] = None
    user_agent: Optional[str] = None
    ip: Optional[str] = None
    current_user_id: Optional[str] = None
    current_user_email: Optional[str] = None

    # Group + group-history fields
    group: Optional[_AuditGroupRef] = None
    active_group: Optional[_AuditGroupRef] = None
    old_group_name: Optional[str] = None
    old_group_type: Optional[str] = None

    # Group ownership / membership change deltas
    group_owners: Optional[list[_AuditUserRef]] = None
    owners_removed_ids_emails: Optional[list[_AuditUserRef]] = None
    owners_added_ids_emails: Optional[list[_AuditUserRef]] = None
    members_removed_ids_emails: Optional[list[_AuditUserRef]] = None
    members_added_ids_emails: Optional[list[_AuditUserRef]] = None
    owners_should_expire_user_id_group_id: Optional[list[_AuditUserGroupMemberRef]] = None
    members_should_expire_user_id_group_id: Optional[list[_AuditUserGroupMemberRef]] = None

    # Role-modify deltas
    role: Optional[_AuditRoleRef] = None
    groups_added_ending_at: Optional[datetime] = None
    owner_groups_removed_ids_names: Optional[list[_AuditRoleRef]] = None
    owner_groups_added_ids_names: Optional[list[_AuditRoleRef]] = None
    owner_groups_should_expire_role_id_group_id: Optional[list[_AuditRoleGroupMapRef]] = None
    groups_removed_ids_names: Optional[list[_AuditRoleRef]] = None
    groups_added_ids_names: Optional[list[_AuditRoleRef]] = None
    groups_should_expire_role_id_group_id: Optional[list[_AuditRoleGroupMapRef]] = None

    # Request-related fields
    request: Optional[_AuditAccessRequestRef] = None
    role_request: Optional[_AuditRoleRequestRef] = None
    group_request: Optional[_AuditGroupRequestRef] = None
    requester: Optional[_AuditUserRef] = None

    # App-related fields
    app: Optional[_AuditAppRef] = None
    old_app_name: Optional[str] = None
    owner_id: Optional[str] = None
    old_app_group_lifecycle_plugin: Optional[str] = None
    old_plugin_data: Optional[dict[str, Any]] = None

    # Tag-related fields
    tag: Optional[_AuditTagRef] = None
    old_tag: Optional[_AuditOldTagRef] = None
    tags_added: Optional[list[_AuditTagRef]] = Field(default=None)
    tags_removed: Optional[list[_AuditTagRef]] = Field(default=None)

    # ---- coercion validators ----
    _coerce_group = field_validator("group", "active_group", mode="before")(
        classmethod(lambda cls, v: _as_group_ref(v))
    )
    _coerce_role = field_validator("role", mode="before")(classmethod(lambda cls, v: _as_role_ref(v)))
    _coerce_app = field_validator("app", mode="before")(classmethod(lambda cls, v: _as_app_ref(v)))
    _coerce_user = field_validator("requester", mode="before")(classmethod(lambda cls, v: _as_user_ref(v)))
    _coerce_user_list = field_validator(
        "group_owners",
        "owners_removed_ids_emails",
        "owners_added_ids_emails",
        "members_removed_ids_emails",
        "members_added_ids_emails",
        mode="before",
    )(classmethod(lambda cls, v: _as_user_ref_list(v)))
    _coerce_user_group_member_list = field_validator(
        "owners_should_expire_user_id_group_id",
        "members_should_expire_user_id_group_id",
        mode="before",
    )(classmethod(lambda cls, v: _as_user_group_member_ref_list(v)))
    _coerce_role_list = field_validator(
        "owner_groups_removed_ids_names",
        "owner_groups_added_ids_names",
        "groups_removed_ids_names",
        "groups_added_ids_names",
        mode="before",
    )(classmethod(lambda cls, v: _as_role_ref_list(v)))
    _coerce_role_group_map_list = field_validator(
        "owner_groups_should_expire_role_id_group_id",
        "groups_should_expire_role_id_group_id",
        mode="before",
    )(classmethod(lambda cls, v: _as_role_group_map_ref_list(v)))
    _coerce_request = field_validator("request", mode="before")(classmethod(lambda cls, v: _as_access_request_ref(v)))
    _coerce_role_request = field_validator("role_request", mode="before")(
        classmethod(lambda cls, v: _as_role_request_ref(v))
    )
    _coerce_group_request = field_validator("group_request", mode="before")(
        classmethod(lambda cls, v: _as_group_request_ref(v))
    )
    _coerce_tag = field_validator("tag", mode="before")(classmethod(lambda cls, v: _as_tag_ref(v)))
    _coerce_old_tag = field_validator("old_tag", mode="before")(classmethod(lambda cls, v: _as_old_tag_ref(v)))
    _coerce_tag_list = field_validator(
        "tags_added",
        "tags_removed",
        mode="before",
    )(classmethod(lambda cls, v: _as_tag_ref_list(v)))


def _format_datetime(value: datetime) -> str:
    """RFC 822 if tz-aware, ISO 8601 if naive — matches the legacy schema."""
    if value.tzinfo is not None:
        return value.strftime("%a, %d %b %Y %H:%M:%S %z")
    return value.isoformat()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return _format_datetime(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)


def _strip_at_path(out: dict[str, Any], dotted: str) -> None:
    """Remove `out[a][b][c]` for `dotted = "a.b.c"`. Silently no-ops on
    miss so call-sites can pass paths that may not always be populated."""
    parts = dotted.split(".")
    cursor: Any = out
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor or cursor[part] is None:
            return
        cursor = cursor[part]
    if isinstance(cursor, dict):
        cursor.pop(parts[-1], None)


class AuditLogSchema:
    """Drop-in replacement for the legacy `AuditLogSchema().dumps(...)`.

    Accepts a `dict` payload of mixed primitives + ORM objects, projects
    each field through `AuditLogPayload`, and returns a JSON string. Honors
    the `exclude=` argument as dotted paths into the dumped dict (the
    handful of call-sites that pass it use shapes like
    `["group.app.id", "group.app.name"]` to drop nested fields). `only=`
    and `partial=` are accepted for signature compatibility but ignored.
    """

    def __init__(
        self,
        *,
        exclude: Optional[list[str]] = None,
        only: Optional[list[str]] = None,
        partial: Optional[bool] = None,
        **_: Any,
    ) -> None:
        self._exclude = list(exclude) if exclude else []

    def dumps(self, data: dict[str, Any], **_: Any) -> str:
        # Validate + coerce ORM instances to the projection models, then
        # dump back to a plain dict so we can apply the exclude paths and
        # honor the `_ALLOWED_NULL_KEYS` retention rule below.
        payload = AuditLogPayload.model_validate(data).model_dump(mode="json", by_alias=False)

        out: dict[str, Any] = {}
        for key in payload:
            value = payload[key]
            # Drop None values except for the keys the legacy `pre_dump`
            # always retained.
            if value is None and key not in _ALLOWED_NULL_KEYS:
                continue
            # `extra="forbid"` rejected unknown keys at validate time, so
            # everything that survives is in the allowlist.
            out[key] = value

        for path in self._exclude:
            _strip_at_path(out, path)

        return json.dumps(out, default=_json_default)
