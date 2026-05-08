"""SQLAlchemy compatibility shim.

The model layer was originally written against Flask-SQLAlchemy's `db.X`
API and the application has not yet been ported off it (POST_MIGRATION_TODO
#1). Until that follow-up lands, this module exposes the same surface on
top of plain SQLAlchemy 2.0:

    - `db.Model`         → declarative `Base`
    - `db.session`       → request-scoped Session via ContextVar
    - `db.Column`, `db.relationship`, `db.ForeignKey`, `db.Index`, `db.func`,
      `db.or_`, `db.not_`, `db.text`, and the column type re-exports
      (`db.BigInteger`, `db.Integer`, `db.Boolean`, `db.Unicode`, `db.DateTime`,
      `db.JSON`, `db.Enum`)
    - `Model.query` proxy supporting `.filter()`, `.first_or_404()`, `.paginate()`

The session is bound to a `ContextVar` so each FastAPI request (or CLI
invocation) gets its own Session. The dependency in `api.database.get_db`
sets and clears this var per request.
"""

from __future__ import annotations

import contextvars
import math
from typing import Any, Callable, Generic, Optional, TypeVar

from fastapi import HTTPException
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    Unicode,
    and_,
    cast,
    func,
    not_,
    or_,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Query as SAQuery,
    Session,
    relationship,
    scoped_session,
    sessionmaker,
)

T = TypeVar("T")


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


class Pagination(Generic[T]):
    """Pagination object used by the `db` shim's `Query.paginate(...)` helper.
    Exposes the `items / page / per_page / total / pages / has_next /
    has_prev / next_num / prev_num` attributes the legacy operations were
    written against; will go away with the shim (POST_MIGRATION_TODO #1)."""

    def __init__(self, items: list[T], page: int, per_page: int, total: int):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, math.ceil(total / per_page)) if per_page > 0 else 1
        self.has_next = page < self.pages
        self.has_prev = page > 1
        self.next_num = page + 1 if self.has_next else page
        self.prev_num = page - 1 if self.has_prev else page


class Query(SAQuery):
    """SQLAlchemy Query subclass with the helpers (`first_or_404`,
    `get_or_404`, `paginate`) the operations layer is written against.
    Goes away with the shim (POST_MIGRATION_TODO #1)."""

    def first_or_404(self, description: str = "Not Found") -> Any:
        obj = self.first()
        if obj is None:
            raise HTTPException(status_code=404, detail=description)
        return obj

    def get_or_404(self, ident: Any, description: str = "Not Found") -> Any:
        obj = self.get(ident)
        if obj is None:
            raise HTTPException(status_code=404, detail=description)
        return obj

    def paginate(self, *, page: int = 1, per_page: int = 50) -> Pagination:
        if per_page <= 0:
            # Sentinel "all"
            items = self.all()
            total = len(items)
            return Pagination(items=items, page=1, per_page=max(total, 1), total=total)
        total = self.order_by(None).count()
        items = self.limit(per_page).offset((page - 1) * per_page).all()
        return Pagination(items=items, page=page, per_page=per_page, total=total)


class _QueryProperty:
    """Class-level descriptor that exposes `Model.query` returning a `Query`
    bound to the active scoped session."""

    def __get__(self, _instance: Any, owner: type[Any]) -> Query:
        return db.session.query(owner)  # type: ignore[return-value]


class _DB:
    """The `db` namespace. Exposes the SQLAlchemy types and helpers the
    models + operations layer were written against, backed by plain
    SQLAlchemy 2.0. Goes away with the shim (POST_MIGRATION_TODO #1)."""

    # Model + metadata
    Model = Base
    metadata = Base.metadata

    # SQLAlchemy types and helpers (re-exports)
    Column = staticmethod(Column)
    ForeignKey = staticmethod(ForeignKey)
    Index = staticmethod(Index)
    BigInteger = BigInteger
    Integer = Integer
    Boolean = Boolean
    Unicode = Unicode
    DateTime = DateTime
    JSON = JSON
    Enum = staticmethod(Enum)
    relationship = staticmethod(relationship)
    func = func
    or_ = staticmethod(or_)
    and_ = staticmethod(and_)
    not_ = staticmethod(not_)
    text = staticmethod(text)
    cast = staticmethod(cast)

    def __init__(self) -> None:
        self._engine: Optional[Engine] = None
        self._sessionmaker: Optional[sessionmaker[Session]] = None
        self._scoped: Optional[scoped_session[Session]] = None

    def init_app(self, *, engine: Engine) -> None:
        """Bind the shim to a SQLAlchemy engine. Must be called once at app
        (or CLI) startup before any ORM operation."""
        self._engine = engine
        self._sessionmaker = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=True,
            query_cls=Query,
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


# Attach the query property so every model has `Model.query`. Done after
# `_DB` is defined because the property delegates to `db.session`.
Base.query = _QueryProperty()


db = _DB()

# Public alias for the shim's class so callers (notably tests) can type-annotate
# parameters that take the shim instance — `_DB` itself is private.
Db = _DB


def get_cloudsql_conn(
    cloudsql_connection_name: str,
    db_user: Optional[str] = "root",
    db_name: Optional[str] = "access",
    uses_public_ip: Optional[bool] = False,
) -> Callable[[], Connector]:
    def _get_conn() -> Connector:
        with Connector() as connector:
            conn = connector.connect(
                cloudsql_connection_name,
                "pg8000",
                user=db_user,
                db=db_name,
                ip_type=IPTypes.PUBLIC if uses_public_ip else IPTypes.PRIVATE,
                enable_iam_auth=True,
            )
            return conn

    return _get_conn


__all__ = [
    "Base",
    "Db",
    "Pagination",
    "Query",
    "_session_scope",
    "db",
    "get_cloudsql_conn",
]
