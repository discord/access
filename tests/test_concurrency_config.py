"""Concurrency + connection-pool configuration.

These guard the two knobs that bound how much work a single server worker
process takes on at once: the anyio threadpool that runs sync (`def`) route
handlers, and the SQLAlchemy QueuePool that backs them. The defaults keep a
burst of expensive requests from driving one worker's memory and connection
use without bound.
"""

from __future__ import annotations

from typing import Any

import anyio
import anyio.to_thread

from api.app import _configure_threadpool_limit
from api.config import settings
from api.database import build_engine


def test_build_engine_applies_pool_settings(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "CLOUDSQL_CONNECTION_NAME", "")
    monkeypatch.setattr(settings, "SQLALCHEMY_DATABASE_URI", "postgresql+pg8000://u:p@localhost:5432/access")
    monkeypatch.setattr(settings, "DB_POOL_SIZE", 7)
    monkeypatch.setattr(settings, "DB_MAX_OVERFLOW", 3)

    engine = build_engine()
    try:
        assert engine.pool.size() == 7
        # max_overflow has no public accessor; QueuePool stores it here.
        assert engine.pool._max_overflow == 3  # type: ignore[attr-defined]
    finally:
        engine.dispose()


def test_build_engine_sqlite_ignores_pool_settings(monkeypatch: Any) -> None:
    # SQLite's pool rejects pool_size/max_overflow; build_engine must not
    # pass them for a sqlite URL.
    monkeypatch.setattr(settings, "CLOUDSQL_CONNECTION_NAME", "")
    monkeypatch.setattr(settings, "SQLALCHEMY_DATABASE_URI", "sqlite://")
    engine = build_engine()
    engine.dispose()


def test_threadpool_limit_applied(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "THREADPOOL_MAX_WORKERS", 5)

    async def _apply_and_read() -> float:
        _configure_threadpool_limit()
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert anyio.run(_apply_and_read) == 5


def test_threadpool_limit_zero_leaves_default(monkeypatch: Any) -> None:
    async def _read_default() -> float:
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    default = anyio.run(_read_default)

    monkeypatch.setattr(settings, "THREADPOOL_MAX_WORKERS", 0)

    async def _apply_and_read() -> float:
        _configure_threadpool_limit()
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert anyio.run(_apply_and_read) == default
