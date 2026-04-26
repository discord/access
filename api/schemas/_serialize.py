"""Safe Pydantic serialization for SQLAlchemy ORM objects.

Many ORM relationships in this codebase are configured with
`lazy="raise_on_sql"` to make implicit lazy loading explicit. When Pydantic
validates an ORM object with `from_attributes=True`, accessing such a
relationship that hasn't been pre-loaded raises `InvalidRequestError`.

This module wraps an ORM object so attribute access returns `None` for
unloaded relationships instead of raising, letting Pydantic fill in the
default. Routes are still responsible for eagerly loading the relationships
they actually need; this just keeps optional embedded fields from blowing up
the response when they're irrelevant.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.exc import InvalidRequestError


_SAFE_SENTINEL = object()


class _SafeAttrProxy:
    """Wrap an ORM object so unloaded relationship access returns None."""

    __slots__ = ("_obj",)

    def __init__(self, obj: Any):
        object.__setattr__(self, "_obj", obj)

    def __getattr__(self, name: str) -> Any:
        try:
            value = getattr(self._obj, name)
        except InvalidRequestError:
            # Distinguish collection relationships (return []) from scalar
            # ones (return None) by inspecting the model's mapper. Falling
            # back to None for any error.
            try:
                from sqlalchemy import inspect as sa_inspect

                mapper = sa_inspect(type(self._obj))
                rel = mapper.relationships.get(name)
                if rel is not None and rel.uselist:
                    return []
            except Exception:
                pass
            return None
        # Recursively wrap nested ORM objects so their attribute access is
        # also safe. Detect ORM objects via the SQLAlchemy `_sa_instance_state`
        # marker.
        if value is None:
            return None
        if hasattr(value, "_sa_instance_state"):
            return _SafeAttrProxy(value)
        if isinstance(value, list):
            return [_SafeAttrProxy(v) if hasattr(v, "_sa_instance_state") else v for v in value]
        return value

    def __iter__(self):
        return iter(self._obj)


def safe_dump(adapter: Any, obj: Any) -> Any:
    """Validate `obj` via the given Pydantic `TypeAdapter` (or BaseModel
    subclass) using `from_attributes=True`, returning a JSON-compatible dict.
    Unloaded relationships surface as `None` instead of raising.
    """
    wrapped = _SafeAttrProxy(obj) if obj is not None else None
    if wrapped is None:
        return None
    if hasattr(adapter, "validate_python"):
        validated = adapter.validate_python(wrapped, from_attributes=True)
        return adapter.dump_python(validated, mode="json")
    # Pydantic BaseModel subclass
    return adapter.model_validate(wrapped).model_dump(mode="json")
