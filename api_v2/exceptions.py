"""
Exception handlers for FastAPI application.
Provides consistent error responses and handles common exceptions.
"""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
import logging

logger = logging.getLogger(__name__)


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
            "body": exc.body if hasattr(exc, 'body') else None
        }
    )


async def pydantic_validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """
    Handle direct Pydantic validation errors.
    """
    logger.warning(f"Pydantic validation error on {request.url}: {exc}")
    
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "message": "Data validation failed",
            "details": exc.errors()
        }
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """
    Handle SQLAlchemy database errors.
    """
    logger.error(f"Database error on {request.url}: {exc}")
    
    # Don't expose internal database errors to clients
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "message": "Internal server error",
            "detail": "A database error occurred"
        }
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions.
    """
    logger.error(f"Unexpected error on {request.url}: {type(exc).__name__}: {exc}")
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "message": "Internal server error",
            "detail": "An unexpected error occurred"
        }
    )