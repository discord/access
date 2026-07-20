"""Pagination plumbing.

Wraps `fastapi-pagination`'s `Page` / `Params` / `paginate` with project-local
defaults — capped `size`, custom default values, the `q` free-text filter, and
the audit endpoints' `transformer=` hook for ORM-row → wire-row factories.

The wire shape is `fastapi-pagination`'s standard `{items, total, page, size,
pages}`. Page numbers are 1-indexed (the standard).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any, TypeVar, overload

from fastapi import Query
from fastapi_pagination import Page as _BasePage
from fastapi_pagination.customization import CustomizedPage, UseParams, UseParamsFields
from fastapi_pagination.default import Params as _DefaultParams
from pydantic import BaseModel, TypeAdapter

DEFAULT_SIZE = 50
MAX_SIZE = 1000

# App-groups embed every group's full membership per item, so this page is
# bound tighter than the default: one page can materialize at most this many
# groups' worth of members regardless of how many groups an app owns.
APP_GROUPS_SIZE = 10


class PageParams(_DefaultParams):
    """Default `Params` subclass: 1-indexed `page`, `size` capped at MAX_SIZE."""

    page: int = Query(1, ge=1, description="Page number")
    size: int = Query(DEFAULT_SIZE, ge=1, le=MAX_SIZE, description="Items per page")


# `Page[T]` re-export with our `PageParams` defaults pre-applied so router
# signatures stay short: `-> Page[OktaUserSummary]`.
T = TypeVar("T")
Page = CustomizedPage[
    _BasePage[T],
    UseParams(PageParams),
]
# `GET /api/apps/{id}/groups` caps `size` at APP_GROUPS_SIZE so one page can't
# load an unbounded number of groups' memberships. Override just the `size`
# field on `Page` rather than defining a whole Params subclass.
AppGroupsPage = CustomizedPage[
    Page[T],
    UseParamsFields(size=Query(APP_GROUPS_SIZE, ge=1, le=APP_GROUPS_SIZE, description="Items per page")),
]


M = TypeVar("M", bound=BaseModel)


@lru_cache(maxsize=None)
def _adapter(model: type[M]) -> TypeAdapter[M]:
    return TypeAdapter(model)


@overload
def validated(model: type[M]) -> Callable[[Sequence[Any]], list[M]]: ...


@overload
def validated(model: Any) -> Callable[[Sequence[Any]], list[Any]]: ...


def validated(model: Any) -> Callable[[Sequence[Any]], list[Any]]:
    """Build a `fastapi-pagination` `transformer=` that validates each ORM row
    through `model` with `from_attributes=True`.

    Typed so `validated(OktaUserGroupMemberDetail)` yields
    `list[OktaUserGroupMemberDetail]`; the fallback overload covers the
    discriminated-union `TypeAliasType` shapes (e.g. `GroupSummary`), which
    aren't a `type[BaseModel]` and resolve to `list[Any]`.

    This reproduces the eager-load safety net the hand-rolled `paginate()` had:
    validation runs here in the handler (session open), so an un-eager-loaded
    `lazy="raise_on_sql"` relationship surfaces as `InvalidRequestError` at the
    pagination boundary — caught by the test suite — instead of lazily loading
    during FastAPI's response serialization. The resulting `Page.items` are
    already-validated model instances, so the later serialization is a no-op
    re-dump."""
    adapter = _adapter(model)
    return lambda rows: [adapter.validate_python(row, from_attributes=True) for row in rows]


__all__ = [
    "APP_GROUPS_SIZE",
    "AppGroupsPage",
    "DEFAULT_SIZE",
    "MAX_SIZE",
    "Page",
    "PageParams",
    "validated",
]
