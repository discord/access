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

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from api.schemas.core_schemas import OktaGroupTagMapDetail
from api.schemas.rfc822 import FlexibleDatetimeOpt


class _UserSummaryForAudit(BaseModel):
    """Mirrors `_user_summary` at audit.py:106-117. Subset of
    `OktaUserSummary` from core_schemas.py — no `updated_at`."""

    id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    deleted_at: FlexibleDatetimeOpt = None
    created_at: FlexibleDatetimeOpt = None


class _AppRefForAudit(BaseModel):
    """Mirrors `_app_ref` at audit.py:120-127."""

    id: str
    name: Optional[str] = None
    deleted_at: FlexibleDatetimeOpt = None


class _RoleGroupRefForAudit(BaseModel):
    """Mirrors `_role_group_ref` at audit.py:146-155."""

    id: str
    type: str
    name: str
    is_managed: Optional[bool] = None
    deleted_at: FlexibleDatetimeOpt = None


class _GroupRefForAudit(BaseModel):
    """Mirrors `_group_ref` at audit.py:130-143 plus the conditional keys
    injected by `_serialize_user_group_member` at audit.py:195-205."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    name: str
    is_owner: Optional[bool] = None
    is_managed: Optional[bool] = None
    deleted_at: FlexibleDatetimeOpt = None
    # `app` is only injected for AppGroup rows.
    app: Optional[_AppRefForAudit] = None
    active_group_tags: list[OktaGroupTagMapDetail] = Field(default_factory=list)
    # Only injected for RoleGroup rows AND only when neither `user_id` nor
    # `group_id` is set on the request.
    active_role_associated_group_member_mappings: Optional[list["_RoleAssociatedMappingForAudit"]] = None
    active_role_associated_group_owner_mappings: Optional[list["_RoleAssociatedMappingForAudit"]] = None

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
    created_at: FlexibleDatetimeOpt = None
    ended_at: FlexibleDatetimeOpt = None
    active_group: Optional[_GroupRefForAudit] = None


class _RoleGroupMappingForAudit(BaseModel):
    """Mirrors `_role_group_mapping_ref` at audit.py:158-165."""

    created_at: FlexibleDatetimeOpt = None
    ended_at: FlexibleDatetimeOpt = None
    role_group: Optional[_RoleGroupRefForAudit] = None


class AuditUserGroupRow(BaseModel):
    """Mirrors `_serialize_user_group_member` (audit.py:190-226)."""

    id: int
    user_id: str
    group_id: str
    role_group_map_id: Optional[int] = None
    is_owner: bool
    should_expire: Optional[bool] = None
    created_reason: str = ""
    created_at: FlexibleDatetimeOpt = None
    updated_at: FlexibleDatetimeOpt = None
    ended_at: FlexibleDatetimeOpt = None
    user: Optional[_UserSummaryForAudit] = None
    group: Optional[_GroupRefForAudit] = None
    role_group_mapping: Optional[_RoleGroupMappingForAudit] = None
    access_request: Optional[_AccessRequestRef] = None
    created_actor: Optional[_UserSummaryForAudit] = None
    ended_actor: Optional[_UserSummaryForAudit] = None


class AuditGroupRoleRow(BaseModel):
    """Mirrors `_serialize_role_group_map` (audit.py:229-245)."""

    id: int
    role_group_id: str
    group_id: str
    is_owner: bool
    should_expire: Optional[bool] = None
    created_reason: str = ""
    created_at: FlexibleDatetimeOpt = None
    ended_at: FlexibleDatetimeOpt = None
    group: Optional[_GroupRefForAudit] = None
    role_group: Optional[_RoleGroupRefForAudit] = None
    created_actor: Optional[_UserSummaryForAudit] = None
    ended_actor: Optional[_UserSummaryForAudit] = None


_GroupRefForAudit.model_rebuild()
