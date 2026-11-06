"""Pydantic schemas for deletion requests."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from api.schemas.core_schemas import AppSummary, GroupRef, OktaUserSummary
from api.schemas.datetimes import FlexibleDatetime
from api.schemas.pagination import SearchQuery
from api.schemas.requests_schemas import ReasonStr, RequestReasonStr
from api.schemas.service_principals import ServicePrincipalDetail


class CreateDeletionRequestBody(BaseModel):
    """Body for POST /api/deletion-requests. Name exactly one of `group_id` and `app_id`."""

    model_config = ConfigDict(extra="ignore")
    group_id: str | None = None
    app_id: str | None = None
    request_reason: RequestReasonStr = ""

    @model_validator(mode="after")
    def _check_one_target(self) -> Self:
        if (self.group_id is None) == (self.app_id is None):
            raise ValueError("Name exactly one of group_id and app_id.")
        return self


class ResolveDeletionRequestBody(BaseModel):
    """Body for PUT /api/deletion-requests/{id}."""

    model_config = ConfigDict(extra="ignore")
    approved: StrictBool
    reason: ReasonStr = ""


class DeletionRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    # Exactly one target is set; `group` / `app` stay set after the target is deleted.
    group_id: str | None = None
    app_id: str | None = None
    group: GroupRef | None = None
    app: AppSummary | None = None
    request_reason: str | None = ""
    resolution_reason: str | None = ""
    resolved_at: FlexibleDatetime | None = None
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    # Exactly one of `requester` and `requester_service_principal` is set.
    requester: OktaUserSummary | None = None
    requester_service_principal: ServicePrincipalDetail | None = None
    resolver: OktaUserSummary | None = None


class SearchDeletionRequestQuery(SearchQuery):
    status: str | None = None
    requester_user_id: str | None = None
    group_id: str | None = None
    app_id: str | None = None
