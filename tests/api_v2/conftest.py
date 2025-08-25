#!/usr/bin/env python3
"""
Pytest configuration for FastAPI test files.
"""
import os
import sys
import pytest
from typing import Generator

# Add the project root to Python path  
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///:memory:"  # Use in-memory database for tests
os.environ["CURRENT_OKTA_USER_EMAIL"] = "test@example.com"

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """
    Create a clean database session for each test.
    Uses only FastAPI database configuration - no Flask dependencies.
    """
    from api_v2.database import get_engine, get_session_local
    from sqlalchemy import MetaData
    
    # Create engine and session for testing
    engine = get_engine()
    
    # For now, we'll create a minimal database setup
    # If specific tables are needed for tests, they can be created explicitly
    metadata = MetaData()
    
    # Create any tables if defined
    metadata.create_all(bind=engine)
    
    # Create session
    SessionLocal = get_session_local()
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.close()
        # Drop all tables for clean state
        metadata.drop_all(bind=engine)


@pytest.fixture
def fastapi_client(db_session):
    """Create a FastAPI TestClient with database setup."""
    from api_v2.main import app
    from api_v2.database import get_db
    
    # Override the database dependency with our test session
    def get_test_db():
        yield db_session
    
    app.dependency_overrides[get_db] = get_test_db
    
    client = TestClient(app)
    
    # Clean up the override after test
    yield client
    
    app.dependency_overrides.clear()