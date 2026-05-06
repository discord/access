"""FastAPI pagination helper.

Builds the legacy `{total, pages, has_next, has_prev, next, prev, results}`
envelope as a typed `PaginationResponse[T]` instance — see
`api/schemas/pagination.py`. URLs use fully qualified URLs (FastAPI idiom);
clients accept either relative or absolute URLs.
"""

from __future__ import annotations

import typing
from typing import Any, Callable, Optional, TypeVar

from pydantic import TypeAdapter
from sqlalchemy.orm import Query
from starlette.requests import Request

from api.schemas.pagination import PaginationResponse

DEFAULT_PER_PAGE = 50
DEFAULT_PAGE_NUMBER = 0
# Hard cap on `per_page`. `per_page=-1` (sentinel for "all") is also clamped
# to this so a single request can't materialize the whole table in memory.
MAX_PER_PAGE = 1000

P = TypeVar("P", bound=PaginationResponse)  # type: ignore[type-arg]


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


def _build_url(request: Request, page: int, per_page: int) -> str:
    return str(request.url.include_query_params(page=page, per_page=per_page))


def _item_adapter(pagination_cls: type[PaginationResponse[Any]]) -> TypeAdapter[Any]:
    """Build a TypeAdapter for the inner row type of a `PaginationResponse[T]`
    subclass — by introspecting `results: list[T]`. Caches one adapter per
    class on the class itself."""
    cached: TypeAdapter[Any] | None = getattr(pagination_cls, "_item_adapter", None)
    if cached is not None:
        return cached
    annotation = pagination_cls.model_fields["results"].annotation
    args = typing.get_args(annotation)
    if not args:
        raise TypeError(f"{pagination_cls.__name__}.results is not a generic list type")
    adapter = TypeAdapter(args[0])
    pagination_cls._item_adapter = adapter  # type: ignore[attr-defined]
    return adapter


def paginate(
    request: Request,
    query: Query[Any],
    pagination_cls: type[P],
    *,
    item_factory: Optional[Callable[[Any], Any]] = None,
    extract: Optional[Callable[[], tuple[int, int]]] = None,
) -> P:
    """Paginate `query` into a typed `PaginationResponse[T]` instance.

    `pagination_cls` is one of the concrete subclasses in
    `api/schemas/pagination.py` (e.g. `UserPagination`). Each row is
    validated via `TypeAdapter(<inner type>).validate_python(row,
    from_attributes=True)` — unloaded SQLAlchemy relationships surface as
    `InvalidRequestError` so eager-load coverage stays enforced.

    `item_factory`, when supplied, is called per ORM row instead of the
    default validation path. Used by audit endpoints where the row is built
    by hand from multiple relationship attributes.

    Page numbers are 0-indexed. `per_page=-1` returns all rows in a single
    page (clamped to `MAX_PER_PAGE`)."""
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
    raw_items = query.limit(per_page).offset(page * per_page).all()

    if item_factory is not None:
        items: list[Any] = [item_factory(item) for item in raw_items]
    else:
        adapter = _item_adapter(pagination_cls)
        items = [adapter.validate_python(item, from_attributes=True) for item in raw_items]

    has_next = page < pages - 1
    has_prev = page > 0

    return pagination_cls(
        total=total,
        pages=pages,
        has_next=has_next,
        has_prev=has_prev,
        # Emit a URL only when the page actually exists. Previously
        # `next_page = min(page + 1, pages - 1)` made the `next` link
        # point at the current page on the last page, so clients that
        # followed the link looped.
        next=_build_url(request, page=page + 1, per_page=per_page) if has_next else None,
        prev=_build_url(request, page=page - 1, per_page=per_page) if has_prev else None,
        results=items,
    )
