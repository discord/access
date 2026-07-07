"""SQLAlchemy async engine creation and the FastAPI `get_db` dependency.

`api.extensions` holds the `db` session/engine facade; this module wires the
engine into it and provides the request-scoped session dependency.
"""

from __future__ import annotations

import ssl
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


def _asyncpg_ssl_connect_args(url: URL) -> tuple[URL, dict[str, Any]]:
    """Translate a libpq ``sslmode=`` query param into asyncpg's ``ssl=`` connect arg.

    pg8000/psycopg2 URLs (and every managed-Postgres connection string) carry
    ``sslmode=``, but SQLAlchemy's asyncpg dialect forwards query params straight
    to ``asyncpg.connect()``, which accepts ``ssl=`` and raises
    ``TypeError: ... unexpected keyword argument 'sslmode'`` otherwise. Translate
    it so existing DATABASE_URIs keep booting on asyncpg. asyncpg accepts
    ``disable``/``allow``/``prefer``/``require`` as plain strings; the
    cert-verifying modes can't pass their cert paths through asyncpg's connect
    kwargs, so build an ``SSLContext`` explicitly and fail loudly on missing
    inputs. Returns the URL with the ``ssl*`` params stripped and the
    ``connect_args`` to pass to ``create_async_engine`` (empty when nothing to do).
    """
    if url.drivername != "postgresql+asyncpg" or "sslmode" not in url.query:
        return url, {}

    def _single(value: str | tuple[str, ...] | None) -> str | None:
        # A URL query param parses as a tuple if repeated; libpq semantics are
        # last-wins. These ssl params are single-valued in practice.
        if isinstance(value, tuple):
            return value[-1] if value else None
        return value

    query = dict(url.query)
    sslmode = _single(query.pop("sslmode"))
    sslrootcert = _single(query.pop("sslrootcert", None))
    sslcert = _single(query.pop("sslcert", None))
    sslkey = _single(query.pop("sslkey", None))
    if sslmode in ("verify-ca", "verify-full"):
        if not sslrootcert:
            raise RuntimeError(f"DATABASE_URI sslmode={sslmode} requires sslrootcert to verify the server certificate")
        if bool(sslcert) != bool(sslkey):
            raise RuntimeError("DATABASE_URI sslcert and sslkey must be set together for client certificate auth")
        ssl_context = ssl.create_default_context(cafile=sslrootcert)
        # verify-full also checks the hostname; verify-ca verifies the chain only.
        ssl_context.check_hostname = sslmode == "verify-full"
        if sslcert and sslkey:
            ssl_context.load_cert_chain(certfile=sslcert, keyfile=sslkey)
        connect_args: dict[str, Any] = {"ssl": ssl_context}
    else:
        # disable / allow / prefer / require: asyncpg takes these as strings.
        connect_args = {"ssl": sslmode}
    return url.set(query=query), connect_args


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
        url, ssl_connect_args = _asyncpg_ssl_connect_args(url)
        if ssl_connect_args:
            kwargs["connect_args"] = ssl_connect_args

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
