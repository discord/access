"""Pydantic schemas for app requests."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from api.config import settings
from api.schemas.core_schemas import OktaUserSummary, RoleGroupRef
from api.schemas.datetimes import FlexibleDatetime
from api.schemas.pagination import SearchQuery
from api.schemas.requests_schemas import (
    _APP_DESC_MAX_LENGTH,
    _APP_NAME_MAX_LENGTH,
    _GROUP_DESC_MAX_LENGTH,
    _GROUP_NAME_MAX_LENGTH,
    _GROUP_NAME_PATTERN_STR,
    ReasonStr,
    RequestReasonStr,
)
from api.schemas.service_principals import ServicePrincipalSummary


class AppRequestGroup(BaseModel):
    """An initial app group named by an app request, with its owners and members."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field(pattern=_GROUP_NAME_PATTERN_STR, min_length=1, max_length=_GROUP_NAME_MAX_LENGTH)
    description: str = Field(default="", max_length=_GROUP_DESC_MAX_LENGTH)
    owner_user_ids: list[str] = Field(default_factory=list)
    owner_role_ids: list[str] = Field(default_factory=list)
    member_user_ids: list[str] = Field(default_factory=list)
    member_role_ids: list[str] = Field(default_factory=list)


class CreateAppRequestBody(BaseModel):
    """Body for POST /api/app-requests.

    `requested_owner_*` name the app's owners, who own and are members of its
    owners group. A user's request that names no owners makes the requester the
    owner; a service principal's request must name one.
    """

    model_config = ConfigDict(extra="ignore")
    requested_app_name: str = Field(min_length=1, max_length=_APP_NAME_MAX_LENGTH)
    requested_app_description: str = Field(default="", max_length=_APP_DESC_MAX_LENGTH)
    requested_app_tags: list[str] = Field(default_factory=list)
    requested_owner_user_ids: list[str] = Field(default_factory=list)
    requested_owner_role_ids: list[str] = Field(default_factory=list)
    requested_app_groups: list[AppRequestGroup] = Field(default_factory=list)
    requested_ownership_ending_at: FlexibleDatetime | None = None
    requested_membership_ending_at: FlexibleDatetime | None = None
    request_reason: RequestReasonStr = ""

    @model_validator(mode="after")
    def _check_description_required(self) -> Self:
        if settings.REQUIRE_DESCRIPTIONS and not self.requested_app_description:
            raise ValueError("Description is required.")
        return self


class ResolveAppRequestBody(BaseModel):
    """Body for PUT /api/app-requests/{id}.

    Each end date, when sent, replaces the requested one on approval; an explicit
    null means indefinite. The app's tag limits still cap the result.
    """

    model_config = ConfigDict(extra="ignore")
    approved: StrictBool
    reason: ReasonStr = ""
    resolved_ownership_ending_at: FlexibleDatetime | None = None
    resolved_membership_ending_at: FlexibleDatetime | None = None


class AppRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    requested_app_name: str
    requested_app_description: str | None = ""
    requested_app_tags: list[str] = Field(default_factory=list)
    requested_owner_user_ids: list[str] = Field(default_factory=list)
    requested_owner_role_ids: list[str] = Field(default_factory=list)
    requested_app_groups: list[AppRequestGroup] = Field(default_factory=list)
    requested_ownership_ending_at: FlexibleDatetime | None = None
    requested_membership_ending_at: FlexibleDatetime | None = None
    request_reason: str | None = ""
    resolved_ownership_ending_at: FlexibleDatetime | None = None
    resolved_membership_ending_at: FlexibleDatetime | None = None
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    approved_app_id: str | None = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    # Exactly one of `requester` and `requester_service_principal` is set.
    requester: OktaUserSummary | None = None
    requester_service_principal: ServicePrincipalSummary | None = None
    # Set when a service principal withdrew its own request; `resolver` names a user resolver.
    resolver: OktaUserSummary | None = None
    resolver_service_principal: ServicePrincipalSummary | None = None
    # The active users and roles named anywhere in the request.
    named_users: list[OktaUserSummary] = Field(default_factory=list)
    named_roles: list[RoleGroupRef] = Field(default_factory=list)

    @field_validator("requested_app_tags", "requested_owner_user_ids", "requested_owner_role_ids", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> Any:
        return v if isinstance(v, list) else []


class SearchAppRequestQuery(SearchQuery):
    status: str | None = None
    requester_user_id: str | None = None
    assignee_user_id: str | None = None
    resolver_user_id: str | None = None
