"""Shared async test helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def db_count(session: AsyncSession, stmt: Select[Any]) -> int:
    """Async replacement for the legacy `Query.count()` test idiom.

    Wraps the statement in a count-over-subquery, matching legacy
    `Query.count()` semantics exactly (joins/distinct included).
    """
    return (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
