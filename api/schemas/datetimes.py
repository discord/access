"""Datetime serializer + parser.

All datetime fields cross the wire as ISO 8601 (e.g. `"2026-04-26T13:45:00Z"`),
which is Pydantic's default. To smooth the cutover from the previous RFC 822
wire format, `parse_datetime_value` is permissive on input — it still accepts
RFC 822 / RFC 2822 strings as well as ISO 8601 strings — so any client still
on the old format keeps working until it catches up.

- `parse_datetime_value` accepts RFC 822 / RFC 2822 strings, ISO 8601 strings,
  and existing `datetime` / `date` objects, returning a `datetime` (or None).
- `FlexibleDatetime` is a Pydantic Annotated type that applies
  `parse_datetime_value` as a `BeforeValidator` and serializes to ISO 8601
  with an explicit `Z` (UTC) marker. Use it in any Pydantic model that
  round-trips a datetime through the API; wrap it in `Optional[...]` for
  nullable fields.

Stored values are naive UTC (see `_to_naive_utc`), so Pydantic's *default*
serialization would emit them with no timezone marker
(`2026-04-26T13:45:00`), which JS `Date` / dayjs parse as **browser-local**
time. The `_to_iso_utc` serializer appends the `Z` so clients parse UTC.

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


def _to_iso_utc(value: Optional[datetime]) -> Optional[str]:
    """Serialize a (naive-UTC) datetime as an explicit-UTC ISO 8601 string
    with a trailing `Z`, so clients parse it as UTC rather than browser-local
    time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


FlexibleDatetime = Annotated[
    datetime,
    BeforeValidator(parse_datetime_value),
    PlainSerializer(_to_iso_utc, when_used="json"),
]
