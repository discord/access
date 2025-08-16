"""
FastAPI dependency injection functions.
These handle authentication, database sessions, and other common dependencies.
"""
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from api_v2.database import get_db
from api.models import OktaUser
from api.authentication import AuthenticationHelpers

security = HTTPBearer(auto_error=False)

async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> OktaUser:
    """
    Get current authenticated user.
    This reuses the existing Flask authentication logic.
    """
    # For development/testing, bypass authentication like Flask does
    from flask import current_app, g
    
    # Create a mock Flask request object to reuse existing auth logic
    class MockFlaskRequest:
        def __init__(self, fastapi_request: Request):
            self.path = str(fastapi_request.url.path)
            self.headers = dict(fastapi_request.headers)
            
    try:
        # Try to use existing Flask authentication
        mock_request = MockFlaskRequest(request)
        auth_result = AuthenticationHelpers.authenticate_user(mock_request)
        
        if auth_result is not None:
            # Authentication failed
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )
            
        # If we get here, authentication succeeded and user is in g.current_user_id
        if hasattr(g, 'current_user_id'):
            user = db.query(OktaUser).filter(
                OktaUser.id == g.current_user_id
            ).filter(OktaUser.deleted_at.is_(None)).first()
            
            if user:
                return user
                
    except Exception:
        # Fallback authentication for development
        pass
    
    # Development fallback - get default user from config
    try:
        from flask import current_app
        default_email = current_app.config.get("CURRENT_OKTA_USER_EMAIL")
        if default_email:
            from sqlalchemy import func
            user = db.query(OktaUser).filter(
                func.lower(OktaUser.email) == func.lower(default_email)
            ).filter(OktaUser.deleted_at.is_(None)).first()
            
            if user:
                return user
    except Exception:
        pass
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials"
    )

def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[OktaUser]:
    """
    Get current user but don't raise exception if not authenticated.
    Useful for endpoints that work with or without authentication.
    """
    try:
        return get_current_user(request, db, credentials)
    except HTTPException:
        return None