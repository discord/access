"""
Health check endpoints for FastAPI.
Simple endpoints to test that the FastAPI app is working.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any

from api_v2.database import get_db
from api_v2.auth import get_current_user_optional
from api.models import OktaUser

router = APIRouter(tags=["health"])

@router.get("/healthz")
async def health_check() -> Dict[str, str]:
    """
    Basic health check endpoint.
    Does not require authentication, similar to Flask version.
    """
    return {"status": "healthy", "version": "2.0.0"}

@router.get("/healthz/db")
async def health_check_database(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Health check that includes database connectivity.
    Tests that we can connect to and query the database.
    """
    try:
        # Simple query to test database connectivity
        user_count = db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).count()
        return {
            "status": "healthy",
            "version": "2.0.0",
            "database": "connected",
            "active_users": user_count
        }
    except Exception as e:
        return {
            "status": "unhealthy", 
            "version": "2.0.0",
            "database": "disconnected",
            "error": str(e)
        }

@router.get("/healthz/auth")
async def health_check_auth(
    current_user: OktaUser = Depends(get_current_user_optional)
) -> Dict[str, Any]:
    """
    Health check that includes authentication test.
    """
    if current_user:
        return {
            "status": "healthy",
            "version": "2.0.0",
            "authentication": "working",
            "user_id": current_user.id,
            "user_email": current_user.email
        }
    else:
        return {
            "status": "healthy", 
            "version": "2.0.0",
            "authentication": "not_authenticated"
        }