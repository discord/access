"""How an approval chooses the end date of the access it grants."""

from __future__ import annotations

from datetime import datetime
from typing import Final

from api.models.tag import earliest_ended_at


class _UseRequested:
    """Sentinel: the approver left the end date as the request had it."""


USE_REQUESTED: Final = _UseRequested()

type EndingAtChoice = datetime | None | _UseRequested


def choose_ending_at(requested: datetime | None, approver: EndingAtChoice, *caps: datetime | None) -> datetime | None:
    """Return the end date an approval grants, as an aware UTC datetime or None for indefinite.

    Args:
        requested: The end date the request asked for.
        approver: The approver's end date, which replaces the requested one (None
            meaning indefinite), or `USE_REQUESTED` to keep it.
        caps: End dates that may only shorten the result, such as a
            conditional-access plugin's.
    """
    chosen = requested if isinstance(approver, _UseRequested) else approver
    return earliest_ended_at(chosen, *caps)
