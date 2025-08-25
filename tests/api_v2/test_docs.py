"""
Test FastAPI documentation endpoints to ensure proper Content-Type handling.
"""

import pytest


def test_docs_endpoint(fastapi_client):
    """Test that /api/v2/docs returns HTML documentation."""
    response = fastapi_client.get("/api/v2/docs")
    content_type = response.headers.get("content-type", "")
    
    # 📄 /api/v2/docs (HTML documentation)
    # Status: {response.status_code}
    # Content-Type: {content_type}
    
    assert response.status_code == 200
    assert "text/html" in content_type
    # ✓ Correct HTML content type


def test_redoc_endpoint(fastapi_client):
    """Test that /api/v2/redoc returns ReDoc documentation."""
    response = fastapi_client.get("/api/v2/redoc")
    content_type = response.headers.get("content-type", "")
    
    # 📄 /api/v2/redoc (ReDoc documentation)
    # Status: {response.status_code}
    # Content-Type: {content_type}
    
    assert response.status_code == 200
    assert "text/html" in content_type
    # ✓ Correct HTML content type


def test_openapi_json_endpoint(fastapi_client):
    """Test that /api/v2/openapi.json returns OpenAPI schema."""
    response = fastapi_client.get("/api/v2/openapi.json")
    content_type = response.headers.get("content-type", "")
    
    # 📄 /api/v2/openapi.json (OpenAPI schema)
    # Status: {response.status_code}
    # Content-Type: {content_type}
    
    assert response.status_code == 200
    assert "application/json" in content_type
    # ✓ Correct JSON content type


def test_healthz_endpoint(fastapi_client):
    """Test that /api/v2/healthz returns JSON API endpoint."""
    response = fastapi_client.get("/api/v2/healthz")
    content_type = response.headers.get("content-type", "")
    
    # 📄 /api/v2/healthz (JSON API endpoint)
    # Status: {response.status_code}
    # Content-Type: {content_type}
    
    assert response.status_code == 200
    assert "application/json" in content_type
    # ✓ Correct JSON content type
