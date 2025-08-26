"""
Tag helper functions for FastAPI.
Pure implementation without Flask dependencies.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from api_v2.models.core_models import Tag


def coalesce_constraints(constraint_key: str, tags: list[Tag]) -> Any:
    """Coalesce constraint values from multiple tags"""
    coalesced_constraint_value = None
    constraint = Tag.CONSTRAINTS[constraint_key]
    for tag in tags:
        if tag.enabled and constraint_key in tag.constraints:
            if coalesced_constraint_value is None:
                coalesced_constraint_value = tag.constraints[constraint_key]
            else:
                coalesced_constraint_value = constraint.coalesce(
                    coalesced_constraint_value, tag.constraints[constraint_key]
                )
    return coalesced_constraint_value


def coalesce_ended_at(
    constraint_key: str,
    tags: list[Tag],
    initial_ended_at: Optional[datetime],
    group_is_managed: bool,
) -> Optional[datetime]:
    """
    Calculate the effective ended_at timestamp based on tag constraints.
    Only apply constraints if the group is managed.
    """
    if not group_is_managed:
        return initial_ended_at

    # Determine the minimum time allowed for group membership and ownership by current group tags
    seconds_limit = coalesce_constraints(constraint_key=constraint_key, tags=tags)
    if seconds_limit is None:
        return initial_ended_at
    else:
        constraint_ended_at = datetime.now(UTC) + timedelta(seconds=seconds_limit)
        if initial_ended_at is None:
            return constraint_ended_at
        else:
            return min(constraint_ended_at, initial_ended_at.replace(tzinfo=UTC))