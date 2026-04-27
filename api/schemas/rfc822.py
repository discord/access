"""RFC822 datetime serializer + parser for Marshmallow JSON-format compatibility.

The legacy Marshmallow schemas configured `DateTime.DEFAULT_FORMAT = "rfc822"`,
so all datetime fields were emitted (and accepted on input) as e.g.
`"Sun, 26 Apr 2026 13:45:00 -0000"`. We preserve both directions:

- `parse_datetime_value` accepts RFC 822 / RFC 2822 strings, ISO 8601 strings,
  and existing `datetime` / `date` objects, returning a `datetime` (or None).
- `RFC822Datetime` / `RFC822DatetimeOpt` are Pydantic Annotated types that
  apply `parse_datetime_value` as a `BeforeValidator` and emit RFC 822 on
  serialization. Use them in any Pydantic model that round-trips a datetime
  through the API.

Routers that read raw `dict[str, Any]` bodies should call
`parse_datetime_value(body.get("ending_at"))` to coerce the wire string into
a `datetime` before passing it to operations or SQLAlchemy columns.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Annotated, Any, Optional

from pydantic import BeforeValidator, PlainSerializer


def parse_datetime_value(value: Any) -> Optional[datetime]:
    """Coerce an incoming wire value into a `datetime`.

    Accepts None, an existing `datetime` (returned as-is), a `date` (promoted
    to midnight UTC), or a string in RFC 822 / RFC 2822 / ISO 8601 form.

    Raises `ValueError` for unrecognized strings or types so Pydantic surfaces
    a clear validation error.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not isinstance(value, str):
        raise ValueError(f"Cannot parse {value!r} as a datetime")
    # Try ISO 8601 first; fall back to RFC 822 / RFC 2822.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"Cannot parse {value!r} as a datetime") from e


def _rfc822(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%a, %d %b %Y %H:%M:%S %z")


RFC822Datetime = Annotated[
    datetime,
    BeforeValidator(parse_datetime_value),
    PlainSerializer(_rfc822, when_used="json"),
]
RFC822DatetimeOpt = Annotated[
    Optional[datetime],
    BeforeValidator(parse_datetime_value),
    PlainSerializer(_rfc822, when_used="json"),
]
