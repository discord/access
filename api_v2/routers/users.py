"""
User endpoints for FastAPI.
Migrated from Flask users_views.py and resources/user.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, nullsfirst, or_
from sqlalchemy.orm import Session, joinedload, selectinload, selectin_polymorphic, with_polymorphic

from api_v2.models import (
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)

from api_v2.database import get_db
from api_v2.auth.middleware import get_authenticated_user
from api_v2.schemas import UserDetail, UserList

# Polymorphic group types for eager loading
ALL_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup, RoleGroup])

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}", response_model=UserDetail)
async def get_user(user_id: str, request: Request, db: Session = Depends(get_db)):
    """
    Get a user by ID or email.
    Support special "@me" user_id to get current user.
    """
    # Get current authenticated user from middleware
    current_user = get_authenticated_user(request)
    
    # Handle special "@me" user_id
    if user_id == "@me":
        user_id = current_user.id

    # Query user by ID or email
    user = (
        db.query(OktaUser)
        .filter(
            or_(
                OktaUser.id == user_id,
                OktaUser.email.ilike(user_id),  # Case-insensitive email search
            )
        )
        .order_by(nullsfirst(OktaUser.deleted_at.desc()))
        .first()
    )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


@router.get("", response_model=list[UserList])
async def list_users(
    q: str | None = Query(None, description="Search query across name, email, and profile"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Items per page"),
    request: Request = None,  # Request is injected by FastAPI, authentication handled by middleware
    db: Session = Depends(get_db),
):
    """
    List users with optional search and pagination.
    """
    # Start with base query - only active users
    query = db.query(OktaUser).filter(OktaUser.deleted_at.is_(None)).order_by(func.lower(OktaUser.email))

    # Apply search filter if provided
    if q and len(q.strip()) > 0:
        like_search = f"%{q.strip()}%"
        query = query.filter(
            or_(
                OktaUser.email.ilike(like_search),
                OktaUser.first_name.ilike(like_search),
                OktaUser.last_name.ilike(like_search),
                OktaUser.display_name.ilike(like_search),
                (OktaUser.first_name + " " + OktaUser.last_name).ilike(like_search),
                # Note: Complex JSON search from Flask version not implemented yet
                # Can be added later if needed
            )
        )

    # Apply pagination
    offset = (page - 1) * per_page
    users = query.offset(offset).limit(per_page).all()

    return users


@router.get("/{user_id}/audit")
async def get_user_audit(user_id: str):
    """
    Redirect to audit endpoint for user audit logs.
    Note: This is a temporary redirect - audit functionality to be implemented later.
    """
    raise HTTPException(status_code=501, detail="User audit endpoint not yet implemented in FastAPI version")
