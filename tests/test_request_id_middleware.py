"""Tests for `RequestIdMiddleware`'s session teardown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from api.extensions import _session_scope, db as _db
from api.middleware import RequestIdMiddleware


async def test_cancelled_request_returns_its_db_connection(tmp_path: Path) -> None:
    """A request cancelled mid-handler (the client disconnected) must still
    return its pooled connection; the middleware shields its teardown."""
    # File-backed so the pool is a real AsyncAdaptedQueuePool (the `db`
    # fixture's in-memory StaticPool never returns connections).
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'request_cancel.db'}")
    _db.init_app(engine=engine)
    try:
        checked_out = anyio.Event()

        async def _hung_handler(_scope: Any, _receive: Any, _send: Any) -> None:
            await _db.session.execute(select(1))
            checked_out.set()
            await anyio.sleep_forever()

        middleware = RequestIdMiddleware(_hung_handler)
        request_sent = False

        async def _receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        async def _send(_message: Any) -> None:
            return None

        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}

        # The middleware only tears down a scope it owns, as in production.
        token = _session_scope.set("__default__")
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(middleware, scope, _receive, _send)
                await checked_out.wait()
                assert engine.pool.checkedout() == 1
                tg.cancel_scope.cancel()
        finally:
            _session_scope.reset(token)

        assert engine.pool.checkedout() == 0, "cancelled request leaked a pooled DB connection"
    finally:
        await engine.dispose()
