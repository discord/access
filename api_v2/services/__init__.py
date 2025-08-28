"""
Services module for API v2.
"""

# Import okta as a module-level attribute for backward compatibility
from api_v2.services.okta_service import get_okta_service

okta = get_okta_service()

__all__ = ["okta"]
