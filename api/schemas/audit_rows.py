"""Pydantic response schemas for the audit endpoints.

The audit router (`api/routers/audit.py`) builds each row by hand via
`_audit_user_group_row` and `_audit_group_role_row`, calling these schemas
directly to construct nested Pydantic instances.

Key design point: the audit row helpers in `audit.py` (`_group_ref_for_audit`,
`_role_group_ref_for_audit`, `_user_summary_for_audit`,
`_role_group_mapping_for_audit`, `_role_associated_mapping_for_audit`)
produce *different* projections than the `GroupRef`/`RoleGroupRef`/
`OktaUserSummary` shapes from `core_schemas.py` — fewer fields, no
`description`, `is_owner` always present (null for non-AppGroup), etc.
The audit-specific refs below mirror each row helper exactly.

Wire-shape gotcha: a few keys inside `_GroupRefForAudit` are emitted
*conditionally* by the audit serializer — `app` only when the row is an
`AppGroup`; `active_role_associated_group_*_mappings` only when neither
`user_id` nor `group_id` was set on the request. The keys are *absent* from
the dict in those cases (not `null`). A model-level `@model_serializer` drops
those keys when they're `None` so the wire shape stays identical.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from api.schemas.core_schemas import OktaGroupTagMapDetail
from api.schemas.datetimes import FlexibleDatetime


class _UserSummaryForAudit(BaseModel):
    """Mirrors `_user_summary` at audit.py:106-117. Subset of
    `OktaUserSummary` from core_schemas.py — no `updated_at`."""

    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    deleted_at: FlexibleDatetime | None = None
    created_at: FlexibleDatetime | None = None


class _AppRefForAudit(BaseModel):
    """Mirrors `_app_ref` at audit.py:120-127."""

    id: str
    name: str | None = None
    deleted_at: FlexibleDatetime | None = None


class _RoleGroupRefForAudit(BaseModel):
    """Mirrors `_role_group_ref` at audit.py:146-155."""

    id: str
    type: str
    name: str
    is_managed: bool | None = None
    deleted_at: FlexibleDatetime | None = None


class _GroupRefForAudit(BaseModel):
    """Mirrors `_group_ref` at audit.py:130-143 plus the conditional keys
    injected by `_serialize_user_group_member` at audit.py:195-205."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    name: str
    is_owner: bool | None = None
    is_managed: bool | None = None
    deleted_at: FlexibleDatetime | None = None
    # `app` is only injected for AppGroup rows.
    app: _AppRefForAudit | None = None
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)
    # Only injected for RoleGroup rows AND only when neither `user_id` nor
    # `group_id` is set on the request.
    active_role_associated_group_member_mappings: list[_RoleAssociatedMappingForAudit] | None = None
    active_role_associated_group_owner_mappings: list[_RoleAssociatedMappingForAudit] | None = None

    @model_serializer(mode="wrap")
    def _drop_conditional_keys(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        # Match legacy: `app` key only appears on AppGroup rows.
        if data.get("type") != "app_group":
            data.pop("app", None)
        # Match legacy: role-association lists are absent (not null) when not
        # populated.
        if data.get("active_role_associated_group_member_mappings") is None:
            data.pop("active_role_associated_group_member_mappings", None)
        if data.get("active_role_associated_group_owner_mappings") is None:
            data.pop("active_role_associated_group_owner_mappings", None)
        return data


class _AccessRequestRef(BaseModel):
    """Mirrors `_access_request_ref` at audit.py:184-187."""

    id: str


class _RoleAssociatedMappingForAudit(BaseModel):
    """Mirrors `_role_associated_mapping_for_audit` at audit.py:168-181."""

    id: int
    is_owner: bool
    created_at: FlexibleDatetime | None = None
    ended_at: FlexibleDatetime | None = None
    active_group: _GroupRefForAudit | None = None


class _RoleGroupMappingForAudit(BaseModel):
    """Mirrors `_role_group_mapping_ref` at audit.py:158-165."""

    created_at: FlexibleDatetime | None = None
    ended_at: FlexibleDatetime | None = None
    role_group: _RoleGroupRefForAudit | None = None


class AuditUserGroupRow(BaseModel):
    """Mirrors `_serialize_user_group_member` (audit.py:190-226)."""

    id: int
    user_id: str
    group_id: str
    role_group_map_id: int | None = None
    is_owner: bool
    should_expire: bool | None = None
    created_reason: str = ""
    created_at: FlexibleDatetime | None = None
    updated_at: FlexibleDatetime | None = None
    ended_at: FlexibleDatetime | None = None
    user: _UserSummaryForAudit | None = None
    group: _GroupRefForAudit | None = None
    role_group_mapping: _RoleGroupMappingForAudit | None = None
    access_request: _AccessRequestRef | None = None
    created_actor: _UserSummaryForAudit | None = None
    ended_actor: _UserSummaryForAudit | None = None


class AuditGroupRoleRow(BaseModel):
    """Mirrors `_serialize_role_group_map` (audit.py:229-245)."""

    id: int
    role_group_id: str
    group_id: str
    is_owner: bool
    should_expire: bool | None = None
    created_reason: str = ""
    created_at: FlexibleDatetime | None = None
    ended_at: FlexibleDatetime | None = None
    group: _GroupRefForAudit | None = None
    role_group: _RoleGroupRefForAudit | None = None
    created_actor: _UserSummaryForAudit | None = None
    ended_actor: _UserSummaryForAudit | None = None


_GroupRefForAudit.model_rebuild()
