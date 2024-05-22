"""Simple helper to paginate query"""

from typing import Any, Dict, Optional, Tuple

from flask import request, url_for
from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema

DEFAULT_PAGE_SIZE = 50
DEFAULT_PAGE_NUMBER = 0


def extract_pagination(
    page: Optional[int] = None, per_page: Optional[int] = None, **request_args: Dict[str, Any]
) -> Tuple[int, int, Dict[str, Any]]:
    page = int(page) if page is not None else DEFAULT_PAGE_NUMBER
    per_page = int(per_page) if per_page is not None else DEFAULT_PAGE_SIZE
    return page, per_page, request_args


def paginate(query: SQLAlchemy, schema: Schema) -> Dict[str, Any]:
    page, per_page, other_request_args = extract_pagination(**request.args)  # type: ignore[arg-type]
    # Make pagination index 0 based instead of 1 based
    if per_page is -1:
        per_page = query.count()
    page_obj = query.paginate(page=page + 1, per_page=per_page)
    endpoint = request.endpoint if request.endpoint is not None else ""
    view_args = request.view_args if request.view_args is not None else {}
    next_ = url_for(
        endpoint,
        page=page_obj.next_num - 1 if page_obj.has_next else page_obj.page - 1,
        per_page=per_page,
        **other_request_args,
        **view_args,
    )
    prev = url_for(
        endpoint,
        page=page_obj.prev_num - 1 if page_obj.has_prev else page_obj.page - 1,
        per_page=per_page,
        **other_request_args,
        **view_args,
    )

    return {
        "total": page_obj.total,
        "pages": page_obj.pages,
        "next": next_,
        "prev": prev,
        "results": schema.dump(page_obj.items),
    }
