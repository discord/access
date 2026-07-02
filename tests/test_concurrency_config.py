"""Concurrency + connection-pool configuration (async engine).

Guards the two knobs that bound how much work a single server worker takes on
at once: the SQLAlchemy pool backing the async engine — the primary backpressure
lever now that route handlers run on the event loop rather than FastAPI's
sync-route threadpool — and anyio's worker-thread limiter, which still backs the
to-thread / sync-dependency paths.
"""

from __future__ import annotations

from typing import Any

import anyio
import anyio.to_thread

from api.app import _configure_threadpool_limit
from api.config import settings
from api.database import build_async_engine


async def test_build_async_engine_applies_pool_settings(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "CLOUDSQL_CONNECTION_NAME", "")
    monkeypatch.setattr(settings, "SQLALCHEMY_DATABASE_URI", "postgresql://u:p@localhost:5432/access")
    monkeypatch.setattr(settings, "DB_POOL_SIZE", 7)
    monkeypatch.setattr(settings, "DB_MAX_OVERFLOW", 3)

    engine = build_async_engine()
    try:
        # The async engine wraps a sync Engine whose pool carries the sizing.
        assert engine.sync_engine.pool.size() == 7
        # max_overflow has no public accessor; QueuePool stores it here.
        assert engine.sync_engine.pool._max_overflow == 3  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


async def test_build_async_engine_sqlite_ignores_pool_settings(monkeypatch: Any) -> None:
    # SQLite (aiosqlite) uses a single-connection pool that rejects
    # pool_size/max_overflow; build_async_engine must not pass them.
    monkeypatch.setattr(settings, "CLOUDSQL_CONNECTION_NAME", "")
    monkeypatch.setattr(settings, "SQLALCHEMY_DATABASE_URI", "sqlite://")
    engine = build_async_engine()
    await engine.dispose()


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
