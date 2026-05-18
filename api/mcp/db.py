"""Per-tool database session helper.

REST routers go through ``Depends(get_db)`` in ``api/database.py`` which
commits or rolls back on the way out. MCP tools don't run through FastAPI
dependencies, so they need an equivalent. This context manager wraps the
existing scoped session and applies the same commit-on-success /
rollback-on-exception behavior.

Read-only tools can use ``db.session`` directly without going through this
helper — the request-scoped session is already wired up by
``RequestIdMiddleware`` for any incoming HTTP request (including the
``/mcp`` route). The helper exists for tools that need the commit/rollback
discipline a write operation requires.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from api.extensions import db as _db_shim


@contextmanager
def mcp_db_session() -> Iterator[Session]:
    """Yield the request-scoped DB session, committing on success and
    rolling back on exception. Matches the contract of
    ``api/database.py::get_db`` so write tools behave the same way as
    REST handlers."""
    session = _db_shim.session
    try:
        yield session
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    else:
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
