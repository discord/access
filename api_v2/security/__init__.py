"""
Security components for FastAPI.
"""

from .middleware import SecurityHeadersMiddleware

__all__ = ["SecurityHeadersMiddleware"]
