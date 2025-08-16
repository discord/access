"""
Security middleware for FastAPI.
Replicates the security headers and policies from the Flask app.
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

DOC_PATHS = ["/api/v2/docs", "/api/v2/redoc", "/api/v2/openapi.json"]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all API responses.
    Replicates the behavior from Flask app.py's add_headers function.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Apply security headers to all API endpoints
        if request.url.path.startswith("/api"):
            response.headers["X-XSS-Protection"] = "0"
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

            # Only set Content-Type to JSON for actual API endpoints, not docs
            if not any(request.url.path.startswith(path) for path in DOC_PATHS):
                response.headers["Content-Type"] = "application/json; charset=utf-8"

        return response
