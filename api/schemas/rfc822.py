"""RFC822 datetime serializer for Marshmallow JSON-format compatibility.

The legacy Marshmallow schemas configured `DateTime.DEFAULT_FORMAT = "rfc822"`,
so all datetime fields were emitted as e.g. `"Sun, 26 Apr 2026 13:45:00 -0000"`.
We preserve that wire format via a Pydantic `PlainSerializer`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import PlainSerializer


def _rfc822(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%a, %d %b %Y %H:%M:%S %z")


RFC822Datetime = Annotated[datetime, PlainSerializer(_rfc822, when_used="json")]
RFC822DatetimeOpt = Annotated[Optional[datetime], PlainSerializer(_rfc822, when_used="json")]
