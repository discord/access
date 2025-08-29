"""
FastAPI application main entry point.
This runs alongside the existing Flask app during the migration.
"""

import logging
import sys
from os import path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from api_v2.auth import AuthenticationMiddleware
from api_v2.config import settings
from api_v2.exceptions import (
    general_exception_handler,
    http_not_found_exception_handler,
    pydantic_validation_exception_handler,
    sqlalchemy_exception_handler,
    validation_exception_handler,
)
from api_v2.log_filters import TokenSanitizingFilter
from api_v2.routers import groups, health, users
from api_v2.security import SecurityHeadersMiddleware
from api_v2.services import okta


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
    if settings.okta_domain and settings.okta_api_token:
        okta.configure(
            settings.okta_domain, settings.okta_api_token, use_group_owners_api=settings.okta_use_group_owners_api
        )
        logging.info("✓ Okta service initialized")


# Setup logging and services on module import
setup_logging()
initialize_services()

app = FastAPI(
    title="Access API v2",
    description="FastAPI version of Access",
    version="2.0.0",
    docs_url="/api/v2/docs",
    redoc_url="/api/v2/redoc",
    openapi_url="/api/v2/openapi.json",
)

# Add CORS middleware (for development)
if settings.env == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],  # React dev server
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "baggage", "sentry-trace"],
    )

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add authentication middleware (opt-out approach - all routes protected by default)
app.add_middleware(AuthenticationMiddleware)

# Add exception handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(404, http_not_found_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include routers
app.include_router(health.router, prefix="/api/v2")
app.include_router(users.router, prefix="/api/v2")
app.include_router(groups.router, prefix="/api/v2")


app.mount("/", StaticFiles(directory=path.join(path.dirname(__file__), "..", "build"), html=True), name="static")
