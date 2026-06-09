"""Datetime serializer + parser.

All datetime fields cross the wire as ISO 8601 (e.g. `"2026-04-26T13:45:00Z"`),
which is Pydantic's default. To smooth the cutover from the previous RFC 822
wire format, `parse_datetime_value` is permissive on input — it still accepts
RFC 822 / RFC 2822 strings as well as ISO 8601 strings — so any client still
on the old format keeps working until it catches up.

- `parse_datetime_value` accepts RFC 822 / RFC 2822 strings, ISO 8601 strings,
  and existing `datetime` / `date` objects, returning a `datetime` (or None).
- `FlexibleDatetime` / `FlexibleDatetimeOpt` are Pydantic Annotated types that
  apply `parse_datetime_value` as a `BeforeValidator` and rely on Pydantic's
  default ISO 8601 serialization. Use them in any Pydantic model that
  round-trips a datetime through the API.

Routers that read raw `dict[str, Any]` bodies should call
`parse_datetime_value(body.get("ending_at"))` to coerce the wire string into
a `datetime` before passing it to operations or SQLAlchemy columns.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Annotated, Any, Optional

from pydantic import BeforeValidator


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
        return _to_naive_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        raise ValueError(f"Cannot parse {value!r} as a datetime")
    # Try ISO 8601 first; fall back to RFC 822 / RFC 2822.
    try:
        return _to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return _to_naive_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Cannot parse {value!r} as a datetime") from e


def _to_naive_utc(value: datetime) -> datetime:
    """Normalize tz-aware datetimes to naive UTC.

    The SQLAlchemy columns these values land in are `DateTime()` (timezone
    naive). SQLite stores tz-aware datetimes by stripping the offset (so the
    wall-clock time stays the same); Postgres converts to UTC then strips.
    Normalizing here makes the stored value identical on both backends.
    """
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


FlexibleDatetime = Annotated[datetime, BeforeValidator(parse_datetime_value)]
FlexibleDatetimeOpt = Annotated[Optional[datetime], BeforeValidator(parse_datetime_value)]
