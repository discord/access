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

from api.schemas._serialize import safe_dump

DEFAULT_PER_PAGE = 50
DEFAULT_PAGE_NUMBER = 0


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

    Uses `safe_dump` so unloaded SQLAlchemy relationships surface as None
    rather than raising InvalidRequestError.
    """
    if isinstance(schema, TypeAdapter):
        return safe_dump(schema, item)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return safe_dump(schema, item)
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
    """Paginate `query` into the legacy envelope.

    Page numbers are 0-indexed (matching the legacy Flask API). `per_page=-1`
    returns all rows in a single page.
    """
    page, per_page = extract() if extract else extract_pagination(request)

    if per_page == -1:
        items = query.all()
        total = len(items)
        return {
            "total": total,
            "pages": 1,
            "next": _build_url(request, page=0, per_page=-1),
            "prev": _build_url(request, page=0, per_page=-1),
            "results": [_serialize(item, schema) for item in items],
        }

    if per_page <= 0:
        per_page = DEFAULT_PER_PAGE

    total = query.order_by(None).count()
    pages = max(1, (total + per_page - 1) // per_page)
    items = query.limit(per_page).offset(page * per_page).all()

    next_page = min(page + 1, pages - 1)
    prev_page = max(page - 1, 0)

    return {
        "total": total,
        "pages": pages,
        "next": _build_url(request, page=next_page, per_page=per_page),
        "prev": _build_url(request, page=prev_page, per_page=per_page),
        "results": [_serialize(item, schema) for item in items],
    }
