"""
FastAPI application main entry point.
This runs alongside the existing Flask app during the migration.
"""
import logging
import os
import sys

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from api_v2.exceptions import (
    general_exception_handler,
    pydantic_validation_exception_handler,
    sqlalchemy_exception_handler,
    validation_exception_handler,
)
from api_v2.log_filters import TokenSanitizingFilter
from api_v2.middleware.security import SecurityHeadersMiddleware
from api_v2.routers import groups, health, users


def setup_logging():
    """Configure logging for FastAPI application"""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    
    # Apply token sanitizing filter to prevent sensitive information in logs
    token_filter = TokenSanitizingFilter()
    
    # Apply filter to relevant loggers
    logging.getLogger("fastapi").addFilter(token_filter)
    logging.getLogger("uvicorn").addFilter(token_filter)
    logging.root.addFilter(token_filter)


def initialize_services():
    """Initialize external services like Okta"""
    try:
        from api.services import okta
        
        okta_domain = os.getenv("OKTA_DOMAIN")
        okta_token = os.getenv("OKTA_API_TOKEN")
        use_group_owners = os.getenv("OKTA_USE_GROUP_OWNERS_API", "False") == "True"
        
        if okta_domain and okta_token:
            okta.initialize(okta_domain, okta_token, use_group_owners_api=use_group_owners)
            logging.info("✓ Okta service initialized")
    except ImportError:
        logging.warning("⚠ Okta service not available")


# Setup logging and services on module import
setup_logging()
initialize_services()

app = FastAPI(
    title="Access Management API v2",
    description="FastAPI version of access management system",
    version="2.0.0",
    docs_url="/api/v2/docs",
    redoc_url="/api/v2/redoc",
    openapi_url="/api/v2/openapi.json"
)

# Add CORS middleware (for development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "baggage", "sentry-trace"],
)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add exception handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include routers
app.include_router(health.router, prefix="/api/v2")
app.include_router(users.router, prefix="/api/v2")
app.include_router(groups.router, prefix="/api/v2")

@app.get("/")
async def root():
    """Root endpoint for FastAPI app"""
    return {"message": "Access Management API v2", "status": "running"}