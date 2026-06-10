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
from typing import Any, TypeVar

from fastapi import Query
from fastapi_pagination import Page as _BasePage
from fastapi_pagination.customization import CustomizedPage, UseParams
from fastapi_pagination.default import Params as _DefaultParams
from pydantic import TypeAdapter

DEFAULT_SIZE = 50
MAX_SIZE = 1000


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


@lru_cache(maxsize=None)
def _adapter(model: type) -> TypeAdapter[Any]:
    return TypeAdapter(model)


def validated(model: type) -> Callable[[Sequence[Any]], list[Any]]:
    """Build a `fastapi-pagination` `transformer=` that validates each ORM row
    through `model` with `from_attributes=True`.

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
    "DEFAULT_SIZE",
    "MAX_SIZE",
    "Page",
    "PageParams",
    "validated",
]
