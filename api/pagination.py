"""FastAPI pagination helper.

Builds the legacy `{total, pages, next, prev, results}` envelope. URLs use
fully qualified URLs (FastAPI idiom) — clients should accept either
relative or absolute URLs.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel, TypeAdapter
from sqlalchemy.orm import Query
from starlette.requests import Request

from api.schemas._serialize import dump_orm

DEFAULT_PER_PAGE = 50
DEFAULT_PAGE_NUMBER = 0
# Hard cap on `per_page`. `per_page=-1` (sentinel for "all") is also clamped
# to this so a single request can't materialize the whole table in memory.
MAX_PER_PAGE = 1000


def extract_pagination(request: Request) -> tuple[int, int]:
    try:
        page = int(request.query_params.get("page", DEFAULT_PAGE_NUMBER))
    except (TypeError, ValueError):
        page = DEFAULT_PAGE_NUMBER
    try:
        per_page = int(request.query_params.get("per_page", DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    return page, per_page


def _serialize(item: Any, schema: Any) -> Any:
    """Convert an ORM object via a Pydantic schema, TypeAdapter, or callable.

    Uses `dump_orm` so unloaded SQLAlchemy relationships surface as None
    rather than raising InvalidRequestError.
    """
    if isinstance(schema, TypeAdapter):
        return dump_orm(schema, item)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return dump_orm(schema, item)
    if callable(schema):
        return schema(item)
    raise TypeError(f"Unsupported schema type: {type(schema)}")


def _build_url(request: Request, page: int, per_page: int) -> str:
    return str(request.url.include_query_params(page=page, per_page=per_page))


def paginate(
    request: Request,
    query: Query[Any],
    schema: Any,
    *,
    extract: Optional[Callable[[], tuple[int, int]]] = None,
) -> dict[str, Any]:
    """Paginate `query` into the `{total, pages, next, prev, results}` wire
    envelope the React frontend expects. Page numbers are 0-indexed.
    `per_page=-1` returns all rows in a single page."""
    page, per_page = extract() if extract else extract_pagination(request)

    # Cap `per_page=-1` (sentinel "all") and any positive value above
    # MAX_PER_PAGE at MAX_PER_PAGE — a small DoS shield against requests
    # like `?per_page=-1` that would otherwise stream multi-GB of ORM
    # objects into memory per request.
    if per_page == -1 or per_page > MAX_PER_PAGE:
        per_page = MAX_PER_PAGE
    elif per_page <= 0:
        per_page = DEFAULT_PER_PAGE

    total = query.order_by(None).count()
    pages = max(1, (total + per_page - 1) // per_page)
    items = query.limit(per_page).offset(page * per_page).all()

    has_next = page < pages - 1
    has_prev = page > 0

    return {
        "total": total,
        "pages": pages,
        "has_next": has_next,
        "has_prev": has_prev,
        # Emit a URL only when the page actually exists. Previously
        # `next_page = min(page + 1, pages - 1)` made the `next` link
        # point at the current page on the last page, so clients that
        # followed the link looped.
        "next": _build_url(request, page=page + 1, per_page=per_page) if has_next else None,
        "prev": _build_url(request, page=page - 1, per_page=per_page) if has_prev else None,
        "results": [_serialize(item, schema) for item in items],
    }
