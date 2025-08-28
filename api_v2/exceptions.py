"""
Exception handlers for FastAPI application.
Provides consistent error responses and handles common exceptions.
"""

import logging
from os import path

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


INDEX_HTML_PATH = path.join(path.dirname(__file__), "..", "build", "index.html")


async def http_not_found_exception_handler(request: Request, exc: HTTPException) -> Response:
    """
    Handle HTTP exceptions.
    """
    if exc.status_code == 404 and not request.url.path.startswith("/api/v2/"):
        with open(INDEX_HTML_PATH, "rb") as f:
            return HTMLResponse(status_code=200, content=f.read())
    else:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Handle Pydantic validation errors.
    Convert FastAPI validation errors to consistent JSON format.
    """
    logger.warning(f"Validation error on {request.url}: {exc}")

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "message": "Validation failed",
            "details": exc.errors(),
            "body": exc.body if hasattr(exc, "body") else None,
        },
    )


async def pydantic_validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """
    Handle direct Pydantic validation errors.
    """
    logger.warning(f"Pydantic validation error on {request.url}: {exc}")

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST, content={"message": "Data validation failed", "details": exc.errors()}
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """
    Handle SQLAlchemy database errors.
    """
    logger.error(f"Database error on {request.url}: {exc}")

    # Don't expose internal database errors to clients
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error", "detail": "A database error occurred"},
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions.
    """
    logger.error(f"Unexpected error on {request.url}: {type(exc).__name__}: {exc}")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Internal server error", "detail": "An unexpected error occurred"},
    )
