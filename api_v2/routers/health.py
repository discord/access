"""
Health check endpoints for FastAPI.
Migrated from Flask health_check_views.py
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from api_v2.database import get_db
from api_v2.auth.middleware import mark_as_public, get_authenticated_user
from api_v2.models import OktaUser

router = APIRouter(tags=["health"])

@router.get("/healthz")
async def health_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Basic health check endpoint that matches Flask implementation.
    Tests database connectivity by executing SELECT 1.
    Does not require authentication.
    """
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "error": str(e)}
        )

@router.get("/healthz/auth")
async def health_check_auth(request: Request) -> Dict[str, Any]:
    """
    Health check that includes authentication test.
    This endpoint requires authentication via middleware.
    """
    # Get authenticated user from middleware
    try:
        current_user = get_authenticated_user(request)
        return {
            "status": "healthy",
            "version": "2.0.0",
            "authentication": "working",
            "user_id": current_user.id,
            "user_email": current_user.email
        }
    except AttributeError:
        # User not authenticated (shouldn't happen with middleware)
        return {
            "status": "healthy", 
            "version": "2.0.0",
            "authentication": "not_authenticated"
        }