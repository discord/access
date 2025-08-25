#!/usr/bin/env python3
"""
Test script for FastAPI setup.
This validates that our FastAPI app starts correctly and basic endpoints work.
"""


def test_fastapi(fastapi_client):
    """Test FastAPI setup and basic endpoints."""
    # Test root endpoint
    response = fastapi_client.get("/")
    assert response.status_code == 200

    # Test health endpoint
    response = fastapi_client.get("/api/v2/healthz")
    assert response.status_code == 200

    # Test database health endpoint
    response = fastapi_client.get("/api/v2/healthz/db")
    result = response.json()
    assert response.status_code == 200
    assert "status" in result