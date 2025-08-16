"""
Database configuration for FastAPI.
This reuses the existing SQLAlchemy models from the Flask app.
"""
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from api.extensions import db
from api.models import *  # Import all existing models

# Reuse the same database URL configuration logic from Flask
def get_database_url() -> str:
    """Get database URL from Flask config or environment"""
    from flask import current_app
    try:
        # Try to get from Flask app context if available
        return current_app.config['SQLALCHEMY_DATABASE_URI']
    except RuntimeError:
        # Fallback to environment variables
        import os
        from api.config import Config
        config = Config()
        return config.SQLALCHEMY_DATABASE_URI

# Create engine for FastAPI (separate from Flask's)
engine = create_engine(
    get_database_url(),
    poolclass=StaticPool if "sqlite" in get_database_url() else None,
    connect_args={"check_same_thread": False} if "sqlite" in get_database_url() else {},
    echo=False  # Set to True for SQL debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Generator[Session, None, None]:
    """
    Dependency to get database session for FastAPI endpoints.
    This ensures proper session cleanup.
    """
    db_session = SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()