"""Per-tool database session helpers.

REST routers go through ``Depends(get_db)`` in ``api/database.py`` which
commits or rolls back on the way out. MCP tools don't run through FastAPI
dependencies, so they need an equivalent. ``mcp_db_session`` wraps the
scoped session and applies the same commit-on-success /
rollback-on-exception behavior.

Session-scope lifecycle
-----------------------
MCP tool handlers do **not** execute in the ``/mcp`` HTTP request task.
FastMCP's ``StreamableHTTPSessionManager`` runs the server (and therefore
the tool) in its own task, inside the session manager's task group. That
matters for connection lifecycle: ``RequestIdMiddleware`` calls
``db.remove()`` from the *request* task, but a connection a tool checks
out belongs to the *server* task. Closing an async connection from a
different task than the one that opened it does not return it to the pool
— it stays checked out until the garbage collector reaps it, which is
exactly what raises SQLAlchemy's "The garbage collector is trying to
clean up non-checked-in connection ..." warning.

``tool_session_scope`` fixes this: it binds a fresh session scope for the
duration of a single tool call and removes it *in the same task the tool
ran in*, so the connection is returned to the pool by its owning task.
``requires_scope`` (the wrapper every tool passes through) enters it, so
both read-only tools (``db.session`` directly) and write tools
(``mcp_db_session``) are covered without per-tool boilerplate.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from api.extensions import _session_scope, db as _db_shim


@asynccontextmanager
async def tool_session_scope() -> AsyncIterator[None]:
    """Bind a fresh DB session scope for one MCP tool call and tear it
    down in the calling task.

    Each tool invocation gets its own ``async_scoped_session`` key so the
    session it uses (via ``db.session``) is created and removed within the
    FastMCP server task, guaranteeing the connection returns to the pool
    from the task that checked it out. See the module docstring for why
    the ``/mcp`` request task's ``db.remove()`` can't do this.
    """
    token = _session_scope.set(f"mcp-tool-{uuid.uuid4().hex}")
    try:
        yield
    finally:
        try:
            # Swallow teardown errors so a failed close never masks the
            # tool's result (or its own exception) — mirrors the
            # RequestIdMiddleware teardown.
            await _db_shim.remove()
        except Exception:
            pass
        finally:
            try:
                _session_scope.reset(token)
            except ValueError:
                # Scope was set on a context copy; the token isn't valid
                # here. Fall back to the process-global default.
                _session_scope.set("__default__")


@asynccontextmanager
async def mcp_db_session() -> AsyncIterator[AsyncSession]:
    """Yield the request-scoped DB session, committing on success and
    rolling back on exception. Matches the contract of
    ``api/database.py::get_db`` so write tools behave the same way as
    REST handlers."""
    session = _db_shim.session
    try:
        yield session
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        raise
    else:
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
