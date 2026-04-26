"""SQLAlchemy engine creation and the FastAPI `get_db` dependency.

The shim in `api.extensions` holds the `db` namespace. This module wires the
engine into it and provides the request-scoped session dependency.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, Optional

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from api.config import settings
from api.extensions import _session_scope, db, get_cloudsql_conn


def build_engine() -> Engine:
    """Construct the SQLAlchemy engine from settings."""
    kwargs: dict = {}
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
    """FastAPI dependency that yields a request-scoped Session.

    The session is keyed off a ContextVar; in tests the conftest sets the var
    once for the entire test, so we don't manipulate it here. We commit on
    normal exit and rollback on exception.

    Note: we don't reset the contextvar inside this dependency because
    FastAPI runs sync dependencies in a threadpool with a copied context;
    `ContextVar.reset` would raise `ValueError` if called across contexts.
    The session lifecycle (engine + scoped_session) handles cleanup itself.
    """
    try:
        yield db.session
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        raise


DbSession = Annotated[Session, Depends(get_db)]
