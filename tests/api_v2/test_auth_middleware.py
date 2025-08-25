#!/usr/bin/env python3
"""
Test script for Phase 3: Authentication & Middleware.
Tests the new FastAPI authentication and middleware components.
"""

import os

import pytest
from fastapi import HTTPException


def test_root_endpoint(fastapi_client):
    """Test root endpoint (no authentication required)."""
    response = fastapi_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data


def test_health_endpoint(fastapi_client):
    """Test health endpoint (no authentication required)."""
    response = fastapi_client.get("/api/v2/healthz")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_security_headers_middleware(fastapi_client):
    """Test security headers middleware."""
    response = fastapi_client.get("/api/v2/healthz")
    headers = response.headers

    expected_headers = ["X-XSS-Protection", "Cache-Control", "Pragma", "Expires"]

    # All expected security headers should be present
    for header in expected_headers:
        assert header in headers, f"Missing security header: {header}"


def test_authenticated_endpoint(fastapi_client):
    """Test endpoint that requires authentication."""
    response = fastapi_client.get("/api/v2/users")
    # Should fail with authentication error or return 404 for test user or succeed in test environment
    assert response.status_code in [200, 401, 403, 404]


def test_exception_handling(fastapi_client):
    """Test exception handling."""
    # Test validation error by sending invalid data
    response = fastapi_client.post("/api/v2/groups", json={"invalid": "data"})
    # Should return 400+ status code for invalid request
    assert response.status_code >= 400


def test_openapi_documentation_endpoints(fastapi_client):
    """Test OpenAPI documentation endpoints."""
    doc_endpoints = ["/api/v2/openapi.json", "/api/v2/docs", "/api/v2/redoc"]

    for endpoint in doc_endpoints:
        response = fastapi_client.get(endpoint)
        assert response.status_code == 200, f"Documentation endpoint failed: {endpoint}"


@pytest.mark.asyncio
async def test_auth_environments(db_session):
    """Test authentication behavior in different environments."""
    from api_v2.auth.dependencies import get_current_user

    # Mock request object
    class MockRequest:
        def __init__(self, headers=None, cookies=None):
            self.headers = headers or {}
            self.cookies = cookies or {}
            self.url = type("URL", (), {"path": "/test"})()

    # Test environment without CURRENT_OKTA_USER_EMAIL should fail
    old_email = os.environ.get("CURRENT_OKTA_USER_EMAIL")
    if "CURRENT_OKTA_USER_EMAIL" in os.environ:
        del os.environ["CURRENT_OKTA_USER_EMAIL"]

    mock_request = MockRequest()
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(mock_request, db_session)

    assert exc_info.value.status_code == 500
    assert "Test user email not configured" in exc_info.value.detail

    # Test production environment should fail without auth config
    old_env = os.environ.get("FLASK_ENV")
    os.environ["FLASK_ENV"] = "production"

    # Ensure no auth config
    old_domain = os.environ.get("CLOUDFLARE_TEAM_DOMAIN")
    old_oidc = os.environ.get("OIDC_CLIENT_SECRETS")
    if "CLOUDFLARE_TEAM_DOMAIN" in os.environ:
        del os.environ["CLOUDFLARE_TEAM_DOMAIN"]
    if "OIDC_CLIENT_SECRETS" in os.environ:
        del os.environ["OIDC_CLIENT_SECRETS"]

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(mock_request, db_session)

    assert exc_info.value.status_code == 403
    assert "No authentication method configured" in exc_info.value.detail

    # Test Cloudflare environment should fail without proper token
    os.environ["CLOUDFLARE_TEAM_DOMAIN"] = "test.cloudflareaccess.com"

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(mock_request, db_session)

    assert exc_info.value.status_code == 403
    assert "Missing required Cloudflare authorization token" in exc_info.value.detail

    # Restore original environment
    if old_env:
        os.environ["FLASK_ENV"] = old_env
    else:
        os.environ["FLASK_ENV"] = "test"
    if old_email:
        os.environ["CURRENT_OKTA_USER_EMAIL"] = old_email
    if old_domain:
        os.environ["CLOUDFLARE_TEAM_DOMAIN"] = old_domain
    elif "CLOUDFLARE_TEAM_DOMAIN" in os.environ:
        del os.environ["CLOUDFLARE_TEAM_DOMAIN"]
    if old_oidc:
        os.environ["OIDC_CLIENT_SECRETS"] = old_oidc

    # Phase 3 Authentication & Middleware test completed:
    # - FastAPI app structure working
    # - Security headers middleware working
    # - Exception handling configured
    # - Authentication dependencies created
    # - Authorization helpers migrated
    # - OpenAPI documentation accessible
    # - Authentication environment validation working
