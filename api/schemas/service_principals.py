"""Pydantic schemas for service principals."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.models import ServicePrincipalProvider
from api.schemas.core_schemas import OktaUserSummary, RoleGroupRef
from api.schemas.datetimes import FlexibleDatetime
from api.schemas.pagination import SearchQuery

_DISPLAY_NAME_MAX_LENGTH = 100
_SUBJECT_MAX_LENGTH = 255


class ServicePrincipalOwnerDetail(BaseModel):
    """One owner of a service principal: exactly one of `active_user` or
    `active_role_group` is set. Both are null when the owning user or role
    has been deleted."""

    model_config = ConfigDict(from_attributes=True)
    user_id: str | None = None
    role_group_id: str | None = None
    active_user: OktaUserSummary | None = None
    active_role_group: RoleGroupRef | None = None


class ServicePrincipalDetail(BaseModel):
    """A service principal and the users and roles accountable for it."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    display_name: str
    provider: ServicePrincipalProvider
    subject: str
    created_at: FlexibleDatetime
    updated_at: FlexibleDatetime
    deleted_at: FlexibleDatetime | None = None
    owners: list[ServicePrincipalOwnerDetail] = Field(default_factory=list)


class SearchServicePrincipalQuery(SearchQuery):
    pass


class _OwnersMixin(BaseModel):
    owner_user_ids: list[str] = Field(default_factory=list)
    owner_role_ids: list[str] = Field(default_factory=list)


class CreateServicePrincipalBody(_OwnersMixin):
    """Body for POST /api/service-principals. At least one owner is required."""

    model_config = ConfigDict(extra="ignore")
    display_name: str = Field(min_length=1, max_length=_DISPLAY_NAME_MAX_LENGTH)
    provider: ServicePrincipalProvider = ServicePrincipalProvider.CLOUDFLARE
    subject: str = Field(min_length=1, max_length=_SUBJECT_MAX_LENGTH)

    @model_validator(mode="after")
    def _check_owners(self) -> Self:
        if not self.owner_user_ids and not self.owner_role_ids:
            raise ValueError("At least one owner is required.")
        return self


class UpdateServicePrincipalBody(BaseModel):
    """Body for PUT /api/service-principals/{id}. Every field is optional.
    A sent owner list replaces the owners of that kind; the resulting owner
    set must still name at least one owner."""

    model_config = ConfigDict(extra="ignore")
    display_name: str | None = Field(default=None, min_length=1, max_length=_DISPLAY_NAME_MAX_LENGTH)
    subject: str | None = Field(default=None, min_length=1, max_length=_SUBJECT_MAX_LENGTH)
    owner_user_ids: list[str] | None = None
    owner_role_ids: list[str] | None = None
