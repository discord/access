"""
Group endpoints for FastAPI.
Migrated from Flask groups_views.py and resources/group.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, nullsfirst, or_
from sqlalchemy.orm import Session

from api.models import AppGroup, OktaGroup, OktaUser
from api_v2.database import get_db
from api_v2.dependencies import get_current_user
from api_v2.schemas import AppGroupRead, GroupCreate, GroupList, GroupRead, GroupUpdate, OktaGroupRead, RoleGroupRead

router = APIRouter(prefix="/groups", tags=["groups"])


def convert_group_to_schema(group: OktaGroup) -> GroupRead:
    """
    Convert a SQLAlchemy group model to the appropriate Pydantic schema.
    Handles the polymorphic dispatch based on group type.
    """
    if group.type == "okta_group":
        return OktaGroupRead.model_validate(group)
    elif group.type == "role_group":
        return RoleGroupRead.model_validate(group)
    elif group.type == "app_group":
        # For app groups, we need the additional fields
        app_group = group  # This should be an AppGroup instance
        return AppGroupRead.model_validate(app_group)
    else:
        # Fallback to base okta group
        return OktaGroupRead.model_validate(group)


@router.get("/{group_id}")
async def get_group(
    group_id: str, current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> GroupRead:
    """
    Get a group by ID.
    Returns the appropriate group type based on the discriminator.
    """
    # Query for the group - need to handle polymorphic loading
    group = (
        db.query(OktaGroup).filter(OktaGroup.id == group_id).order_by(nullsfirst(OktaGroup.deleted_at.desc())).first()
    )

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    return convert_group_to_schema(group)


@router.get("", response_model=list[GroupList])
async def list_groups(
    q: str | None = Query(None, description="Search query across name and description"),
    type_filter: str | None = Query(None, alias="type", description="Filter by group type"),
    app_id: str | None = Query(None, description="Filter by application ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Items per page"),
    current_user: OktaUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[GroupList]:
    """
    List groups with optional search and pagination.
    """
    # Start with base query - only active groups
    query = db.query(OktaGroup).filter(OktaGroup.deleted_at.is_(None)).order_by(func.lower(OktaGroup.name))

    # Apply search filter if provided
    if q and len(q.strip()) > 0:
        like_search = f"%{q.strip()}%"
        query = query.filter(or_(OktaGroup.name.ilike(like_search), OktaGroup.description.ilike(like_search)))

    # Apply type filter
    if type_filter:
        query = query.filter(OktaGroup.type == type_filter)

    # Apply app filter for app groups
    if app_id:
        # Join with AppGroup table to filter by app_id
        query = query.join(AppGroup).filter(AppGroup.app_id == app_id)

    # Apply pagination
    offset = (page - 1) * per_page
    groups = query.offset(offset).limit(per_page).all()

    # Convert to GroupList schema
    result = []
    for group in groups:
        group_data = {
            "id": group.id,
            "created_at": group.created_at,
            "updated_at": group.updated_at,
            "deleted_at": group.deleted_at,
            "type": group.type,
            "name": group.name,
            "description": group.description,
            "is_managed": group.is_managed,
            "externally_managed_data": group.externally_managed_data,
            "plugin_data": group.plugin_data,
        }

        # Add app-specific fields for app groups
        if group.type == "app_group" and hasattr(group, "app_id"):
            group_data["app_id"] = group.app_id
            group_data["is_owner"] = getattr(group, "is_owner", False)
        else:
            group_data["app_id"] = None
            group_data["is_owner"] = None

        result.append(GroupList(**group_data))

    return result


@router.post("", status_code=201)
async def create_group(
    group_data: GroupCreate, current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> GroupRead:
    """
    Create a new group.
    Note: This is a placeholder - full implementation would require
    the operations layer from the Flask app.
    """
    raise HTTPException(status_code=501, detail="Group creation not yet implemented in FastAPI version")


@router.put("/{group_id}")
async def update_group(
    group_id: str,
    group_data: GroupUpdate,
    current_user: OktaUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GroupRead:
    """
    Update an existing group.
    Note: This is a placeholder - full implementation would require
    the operations layer from the Flask app.
    """
    raise HTTPException(status_code=501, detail="Group update not yet implemented in FastAPI version")


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: str, current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Delete a group.
    Note: This is a placeholder - full implementation would require
    the operations layer from the Flask app.
    """
    raise HTTPException(status_code=501, detail="Group deletion not yet implemented in FastAPI version")


@router.get("/{group_id}/members")
async def get_group_members(
    group_id: str, current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Get group members and owners.
    Note: This is a placeholder - would need membership schemas.
    """
    raise HTTPException(status_code=501, detail="Group members endpoint not yet implemented in FastAPI version")
