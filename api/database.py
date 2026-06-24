"""SQLAlchemy async engine creation and the FastAPI `get_db` dependency.

`api.extensions` holds the `db` session/engine facade; this module wires the
engine into it and provides the request-scoped session dependency.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from api.config import settings
from api.extensions import db, get_cloudsql_async_conn

# Deployment URLs predating the async engine keep working: legacy sync driver
# names are normalized to their async equivalents.
_ASYNC_DRIVERS = {
    "postgresql": "postgresql+asyncpg",
    "postgres": "postgresql+asyncpg",
    "postgresql+pg8000": "postgresql+asyncpg",
    "postgresql+psycopg2": "postgresql+asyncpg",
    "sqlite": "sqlite+aiosqlite",
    "sqlite+pysqlite": "sqlite+aiosqlite",
}


def to_async_url(url_str: str) -> URL:
    """Normalize a database URL to an async driver."""
    url = make_url(url_str)
    if url.drivername in _ASYNC_DRIVERS:
        url = url.set(drivername=_ASYNC_DRIVERS[url.drivername])
    return url


def build_async_engine() -> AsyncEngine:
    """Construct the SQLAlchemy async engine from settings."""
    kwargs: dict[str, Any] = {}
    if settings.SQLALCHEMY_ECHO:
        kwargs["echo"] = True
    if settings.CLOUDSQL_CONNECTION_NAME:
        kwargs["async_creator"] = get_cloudsql_async_conn(
            cloudsql_connection_name=settings.CLOUDSQL_CONNECTION_NAME,
            db_user=settings.DATABASE_USER,
            db_name=settings.DATABASE_NAME,
            uses_public_ip=settings.DATABASE_USES_PUBLIC_IP,
        )
        # CloudSQL connector creator handles connection details
        url = make_url("postgresql+asyncpg://")
    else:
        url = to_async_url(settings.SQLALCHEMY_DATABASE_URI or "sqlite:///instance/access.db")

    if not url.drivername.startswith("sqlite"):
        # Bound and harden the async pool. SQLAlchemy's async engine uses an
        # AsyncAdaptedQueuePool whose defaults (size 5 / overflow 10) cap the
        # connections a worker can check out concurrently; under async this
        # pool is the main limit on in-flight queries, so size it from settings.
        # SQLite (aiosqlite) uses a single-connection pool and rejects these.
        kwargs.setdefault("pool_size", settings.DB_POOL_SIZE)
        kwargs.setdefault("max_overflow", settings.DB_MAX_OVERFLOW)
        kwargs.setdefault("pool_timeout", settings.DB_POOL_TIMEOUT)
        kwargs.setdefault("pool_recycle", settings.DB_POOL_RECYCLE)
        kwargs.setdefault("pool_pre_ping", settings.DB_POOL_PRE_PING)

    return create_async_engine(url, **kwargs)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields the request-scoped AsyncSession.

    `RequestIdMiddleware` is responsible for setting the `_session_scope`
    contextvar (so each request gets its own scoped AsyncSession) and for
    calling `db.remove()` when the response has been emitted. This
    dependency only commits or rolls back the session on the way out; it
    does not manipulate the scope or close the session, because the
    response body still needs to be serialized after the dependency
    returns.
    """
    try:
        yield db.session
    except Exception:
        try:
            await db.session.rollback()
        except Exception:
            pass
        raise
    else:
        try:
            await db.session.commit()
        except Exception:
            await db.session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]
