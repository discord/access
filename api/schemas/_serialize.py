"""Strict ORM serialization.

`dump_orm` runs the supplied Pydantic `TypeAdapter` (or `BaseModel` subclass)
over a SQLAlchemy ORM object with `from_attributes=True`. Relationships that
the route forgot to eager-load surface as `InvalidRequestError` — the route
must declare a matching `selectinload` / `joinedload` for every field its
response schema emits. The shared loader helpers live in
`api/routers/_eager.py`.
"""

from __future__ import annotations

from typing import Any


def dump_orm(adapter: Any, obj: Any) -> Any:
    """Validate an ORM object via `adapter` and return JSON-compatible data.

    `adapter` may be a Pydantic `TypeAdapter` or a `BaseModel` subclass.
    Returns `None` when `obj` is `None`."""
    if obj is None:
        return None
    if hasattr(adapter, "validate_python"):
        validated = adapter.validate_python(obj, from_attributes=True)
        return adapter.dump_python(validated, mode="json")
    return adapter.model_validate(obj).model_dump(mode="json")
