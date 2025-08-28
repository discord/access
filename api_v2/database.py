"""
Database configuration for FastAPI.
This reuses the existing SQLAlchemy models from the Flask app.
"""

from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api_v2.config import settings
from api_v2.models import *  # Import all models


def get_database_url() -> str:
    """Get database URL from settings"""
    if settings.database_uri:
        return settings.database_uri
    # Default fallback for development
    return "sqlite:///instance/access.db"


@lru_cache(maxsize=1)
def get_engine():
    """Get or create the SQLAlchemy engine"""
    database_url = get_database_url()
    return create_engine(
        database_url,
        poolclass=StaticPool if "sqlite" in database_url else None,
        connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        echo=settings.sqlalchemy_echo,  # Use settings for SQL debugging
    )


@lru_cache(maxsize=1)
def get_session_local():
    """Get or create the session maker"""
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    """
    Dependency to get database session for FastAPI endpoints.
    This ensures proper session cleanup.
    """
    SessionLocal = get_session_local()
    db_session = SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()
