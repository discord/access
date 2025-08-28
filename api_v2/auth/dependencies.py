"""
FastAPI authentication dependencies.
Provides authentication and authorization logic converted from Flask.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api_v2.database import get_db
from api_v2.models import OktaUser


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> OktaUser:
    """
    Get the current authenticated user.
    This is the main authentication dependency that replicates Flask's authenticate_user.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        OktaUser object

    Raises:
        HTTPException: If authentication fails
    """
    if not hasattr(request.state, "user") and request.state.user_id is None:
        raise AttributeError("User not found in request state. Make sure AuthenticationMiddleware is configured.")
    return request.state.user


async def get_current_user_id(request: Request) -> str:
    """
    Get the current authenticated user ID.
    """
    if not hasattr(request.state, "user_id"):
        raise AttributeError("User ID not found in request state. Make sure AuthenticationMiddleware is configured.")
    return request.state.user_id


# Optional dependency for endpoints that don't require authentication
async def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[OktaUser]:
    """
    Get current user without raising exceptions if not authenticated.
    Useful for endpoints that have optional authentication.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        OktaUser if authenticated, None otherwise
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None
