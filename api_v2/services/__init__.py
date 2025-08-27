"""
Services for FastAPI application.
"""

from api_v2.services.okta_service import OktaService

# Create a global instance that can be initialized
okta = OktaService()

__all__ = [
    "OktaService",
    "okta",
]