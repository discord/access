"""
Database configuration for FastAPI.
This reuses the existing SQLAlchemy models from the Flask app.
"""
import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from api.extensions import db
from api.models import *  # Import all existing models

def get_database_url() -> str:
    """Get database URL from Flask config or environment"""
    from flask import current_app
    try:
        # Try to get from Flask app context if available
        return current_app.config['SQLALCHEMY_DATABASE_URI']
    except RuntimeError:
        # Fallback to environment variables (same as api.config)
        database_uri = os.getenv("DATABASE_URI")
        if database_uri:
            return database_uri
        # Default fallback for development
        return "sqlite:///instance/access.db"

# Global variables for lazy initialization
_engine = None
_SessionLocal = None

def get_engine():
    """Get or create the SQLAlchemy engine"""
    global _engine
    if _engine is None:
        database_url = get_database_url()
        _engine = create_engine(
            database_url,
            poolclass=StaticPool if "sqlite" in database_url else None,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
            echo=False  # Set to True for SQL debugging
        )
    return _engine

def get_session_local():
    """Get or create the session maker"""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal

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