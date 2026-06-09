"""Pagination plumbing.

Wraps `fastapi-pagination`'s `Page` / `Params` / `paginate` with project-local
defaults — capped `size`, custom default values, the `q` free-text filter, and
the audit endpoints' `transformer=` hook for ORM-row → wire-row factories.

The wire shape is `fastapi-pagination`'s standard `{items, total, page, size,
pages}`. Page numbers are 1-indexed (the standard).
"""

from __future__ import annotations

from typing import TypeVar

from fastapi import Query
from fastapi_pagination import Page as _BasePage
from fastapi_pagination.customization import CustomizedPage, UseParams
from fastapi_pagination.default import Params as _DefaultParams

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


__all__ = [
    "DEFAULT_SIZE",
    "MAX_SIZE",
    "Page",
    "PageParams",
]
