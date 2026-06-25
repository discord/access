"""SQLAlchemy session/engine plumbing.

Exposes:

- `Base`: declarative base for all ORM models.
- `db.session`: the request-scoped Session bound to the active `_session_scope`.
- `db.engine`: the configured Engine.
- `db.init_app(engine=...)`, `db.remove()`, `db.create_all()`, `db.drop_all()`.

The session is bound to a `ContextVar` so each FastAPI request (or CLI
invocation) gets its own Session. The dependency in `api.database.get_db`
sets and clears this var per request.
"""

from __future__ import annotations

import contextvars
import threading
from typing import Any, Callable, Optional

from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    scoped_session,
    sessionmaker,
)


# Per-request session scope. The FastAPI dependency sets this to a unique
# request id; CLI/syncer entrypoints set it to a per-run sentinel. Default
# is a single process-global scope identifier so that ad-hoc usage (alembic,
# scripts) just works without explicit setup.
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
    """Session/engine facade. Provides per-request scoped session via
    `db.session`, plus startup/teardown plumbing (`init_app`, `remove`)."""

    def __init__(self) -> None:
        self._engine: Optional[Engine] = None
        self._sessionmaker: Optional[sessionmaker[Session]] = None
        self._scoped: Optional[scoped_session[Session]] = None

    def init_app(self, *, engine: Engine) -> None:
        """Bind the session facade to a SQLAlchemy engine. Must be called
        once at app (or CLI) startup before any ORM operation."""
        self._engine = engine
        self._sessionmaker = sessionmaker(
            bind=engine,
            autoflush=False,
            # Loaded state survives commits. Required for the async flip:
            # expired-attribute access on an AsyncSession raises MissingGreenlet,
            # and operations/syncer keep using ORM objects across mid-flow
            # commits. Attributes assigned SQL expressions (e.g. func.now())
            # still expire at flush and need an explicit refresh before use.
            expire_on_commit=False,
        )
        self._scoped = scoped_session(self._sessionmaker, scopefunc=lambda: _session_scope.get())

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise RuntimeError("db.init_app(engine=...) was not called")
        return self._engine

    @property
    def session(self) -> Session:
        """Returns the current scoped session for the active scope."""
        if self._scoped is None:
            raise RuntimeError("db.init_app(engine=...) was not called")
        return self._scoped()

    def remove(self) -> None:
        """Removes the session for the current scope. Called by the FastAPI
        dependency on request teardown and by CLI entrypoints on exit."""
        if self._scoped is not None:
            self._scoped.remove()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)


db = _DB()

# Public alias for the facade's class so callers (notably tests) can type-annotate
# parameters that take the instance — `_DB` itself is private.
Db = _DB


# Process-wide singleton Connector, shared by every engine/creator for the
# life of the worker. The Connector owns a background event loop + aiohttp
# session and a RefreshAheadCache that renews the ephemeral client cert ahead
# of expiry, and it caches connection info per (instance, enable_iam_auth)
# internally — so a single instance serves every Cloud SQL target.
#
# The previous `with Connector()` built and closed one *per connection*, which
# (a) tore the loop/session down while the background cert refresh to
# sqladmin.googleapis.com was still in flight — racing it and surfacing
# ServerDisconnectedError / "Task pending" errors — and (b) gave every
# connection a fresh empty cache, defeating the refresh-ahead design.
_connector: Optional[Connector] = None
_connector_lock = threading.Lock()


def _get_connector() -> Connector:
    """Return the process-wide Connector, building it on first use.

    Built lazily (double-checked lock; the SQLAlchemy pool may call the creator
    from multiple threads) so the Connector's background thread is spawned
    inside the worker process, after any gunicorn fork — a thread created in a
    preloaded master would not survive fork() and the loop would be dead in the
    workers.
    """
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:
                _connector = Connector()
    return _connector


def get_cloudsql_conn(
    cloudsql_connection_name: str,
    db_user: Optional[str] = "root",
    db_name: Optional[str] = "access",
    uses_public_ip: Optional[bool] = False,
) -> Callable[[], Any]:
    def _get_conn() -> Any:
        return _get_connector().connect(
            cloudsql_connection_name,
            "pg8000",
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
    "get_cloudsql_conn",
]
