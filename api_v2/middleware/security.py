"""
Security middleware for FastAPI.
Replicates the security headers and policies from the Flask app.
"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all API responses.
    Replicates the behavior from Flask app.py's add_headers function.
    """
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Only add headers to API endpoints, not static files or docs
        if request.url.path.startswith("/api") and not request.url.path.startswith("/api/v2/docs"):
            response.headers["X-XSS-Protection"] = "0"
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            
        return response