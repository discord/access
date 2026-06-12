"""SQLAlchemy session/engine plumbing.

Exposes:

- `Base`: declarative base for all ORM models.
- `db.session`: the request-scoped AsyncSession bound to the active
  `_session_scope`.
- `db.engine`: the configured AsyncEngine.
- `db.init_app(engine=...)`, `db.remove()`, `db.create_all()`, `db.drop_all()`.

The session is scoped on a `ContextVar` so each FastAPI request (or CLI
invocation) gets its own AsyncSession. The dependency in `api.database.get_db`
yields it per request; `RequestIdMiddleware` sets and clears the scope.

Concurrency rule: an AsyncSession must never be used concurrently. ContextVars
propagate into tasks spawned with `asyncio.create_task`, so a spawned task
sees the *same* session as its parent — tasks handed to `create_task` /
`gather` / `wait` may only perform network I/O (Okta calls, notification
hooks), never `db.session` access.
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_scoped_session,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase


# Per-request session scope. The FastAPI middleware sets this to a unique
# request id; CLI/syncer entrypoints set it to a per-run sentinel. Default
# is a single process-global scope identifier so that ad-hoc usage (alembic,
# scripts) just works without explicit setup.
#
# Scoping on the ContextVar (rather than `asyncio.current_task`) is load-
# bearing: `BaseHTTPMiddleware.call_next` runs the downstream app in a child
# task, and context copies propagate to child tasks while task identity does
# not — task-scoping would register the handler's session under a key the
# middleware teardown could never remove.
_session_scope: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "access_session_scope", default="__default__"
)


def _camel_to_snake(name: str) -> str:
    """Generate the implicit __tablename__ for a model class, matching the
    naming the model layer was originally written against (and which the
    existing Alembic migrations encode)."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Auto-generate __tablename__ unless explicitly declared, the class is
        # abstract, or it's a polymorphic single-table inheritance child.
        if (
            "__tablename__" not in cls.__dict__
            and "__table__" not in cls.__dict__
            and not cls.__dict__.get("__abstract__", False)
        ):
            # Only auto-generate for direct Base subclasses (not joined-table
            # children that inherit from another mapped class).
            mro = [c for c in cls.__mro__ if c is not cls and c is not Base and c is not object]
            mapped_parents = [c for c in mro if hasattr(c, "__mapper__")]
            if not mapped_parents:
                cls.__tablename__ = _camel_to_snake(cls.__name__)
        super().__init_subclass__(**kwargs)


class _DB:
    """Session/engine facade. Provides per-request scoped AsyncSession via
    `db.session`, plus startup/teardown plumbing (`init_app`, `remove`)."""

    def __init__(self) -> None:
        self._engine: Optional[AsyncEngine] = None
        self._sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None
        self._scoped: Optional[async_scoped_session[AsyncSession]] = None

    def init_app(self, *, engine: AsyncEngine) -> None:
        """Bind the session facade to a SQLAlchemy engine. Must be called
        once at app (or CLI) startup before any ORM operation."""
        self._engine = engine
        self._sessionmaker = async_sessionmaker(
            bind=engine,
            autoflush=False,
            # Loaded state survives commits. Required under async:
            # expired-attribute access on an AsyncSession raises MissingGreenlet,
            # and operations/syncer keep using ORM objects across mid-flow
            # commits. Attributes assigned SQL expressions (e.g. func.now())
            # still expire at flush and need an explicit refresh before use.
            expire_on_commit=False,
        )
        self._scoped = async_scoped_session(self._sessionmaker, scopefunc=lambda: _session_scope.get())

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("db.init_app(engine=...) was not called")
        return self._engine

    @property
    def session(self) -> AsyncSession:
        """Returns the current scoped session for the active scope."""
        if self._scoped is None:
            raise RuntimeError("db.init_app(engine=...) was not called")
        return self._scoped()

    async def remove(self) -> None:
        """Removes (closes) the session for the current scope. Called by the
        middleware on request teardown and by CLI entrypoints on exit."""
        if self._scoped is not None:
            await self._scoped.remove()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


db = _DB()

# Public alias for the facade's class so callers (notably tests) can type-annotate
# parameters that take the instance — `_DB` itself is private.
Db = _DB


def get_cloudsql_async_conn(
    cloudsql_connection_name: str,
    db_user: Optional[str] = "root",
    db_name: Optional[str] = "access",
    uses_public_ip: Optional[bool] = False,
) -> Callable[[], Awaitable[Any]]:
    """Build an `async_creator` for `create_async_engine` that connects to
    Cloud SQL over the Cloud SQL Python Connector with asyncpg + IAM auth.

    A single Connector is created lazily on first connect and bound to the
    running event loop, then reused for the engine's lifetime (the connector
    maintains its own background refresh of ephemeral certificates).
    """
    from google.cloud.sql.connector import Connector, IPTypes

    connector: Optional[Connector] = None

    async def _get_conn() -> Any:
        nonlocal connector
        if connector is None:
            connector = Connector(loop=asyncio.get_running_loop())
        return await connector.connect_async(
            cloudsql_connection_name,
            "asyncpg",
            user=db_user,
            db=db_name,
            ip_type=IPTypes.PUBLIC if uses_public_ip else IPTypes.PRIVATE,
            enable_iam_auth=True,
        )

    return _get_conn


__all__ = [
    "Base",
    "Db",
    "_session_scope",
    "db",
    "get_cloudsql_async_conn",
]
