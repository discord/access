"""Custom `APIRoute` that defaults `response_model_exclude_none=True`.

Wired into every router via `APIRouter(route_class=ExcludeNoneAPIRoute)`.
Without this, FastAPI emits `null` for every absent `Optional` field on the
response model — a Pydantic v2 default we don't want on the wire.
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute


class ExcludeNoneAPIRoute(APIRoute):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("response_model_exclude_none", True)
        super().__init__(*args, **kwargs)
