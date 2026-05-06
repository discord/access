"""SQLAlchemy engine creation and the FastAPI `get_db` dependency.

The shim in `api.extensions` holds the `db` namespace. This module wires the
engine into it and provides the request-scoped session dependency.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from api.config import settings
from api.extensions import db, get_cloudsql_conn


def build_engine() -> Engine:
    """Construct the SQLAlchemy engine from settings."""
    kwargs: dict[str, Any] = {}
    if settings.SQLALCHEMY_ECHO:
        kwargs["echo"] = True
    if settings.CLOUDSQL_CONNECTION_NAME:
        kwargs["creator"] = get_cloudsql_conn(
            cloudsql_connection_name=settings.CLOUDSQL_CONNECTION_NAME,
            db_user=settings.DATABASE_USER,
            db_name=settings.DATABASE_NAME,
            uses_public_ip=settings.DATABASE_USES_PUBLIC_IP,
        )
        # CloudSQL connector creator handles connection details
        url = "postgresql+pg8000://"
    else:
        url = settings.SQLALCHEMY_DATABASE_URI or "sqlite:///instance/access.db"

    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})

    return create_engine(url, **kwargs)


def get_db(request: Request) -> Generator[Session, None, None]:
    """FastAPI dependency that yields the request-scoped Session.

    `RequestIdMiddleware` is responsible for setting the `_session_scope`
    contextvar (so each request gets its own scoped Session) and for
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
            db.session.rollback()
        except Exception:
            pass
        raise
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


DbSession = Annotated[Session, Depends(get_db)]
