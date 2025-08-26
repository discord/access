"""
FastAPI authorization dependencies.
Provides authorization logic converted from Flask authorization system.
"""

import os
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload, with_polymorphic

from api_v2.models import (
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
)
from api_v2.auth.dependencies import get_current_user
from api_v2.database import get_db


class AuthorizationHelpers:
    """Authorization helper methods converted from Flask"""

    @staticmethod
    def is_group_owner(db: Session, group: OktaGroup, current_user: OktaUser) -> bool:
        """Check if the current user is an owner of the group"""
        return (
            db.query(OktaUserGroupMember)
            .filter(OktaUserGroupMember.group_id == group.id)
            .filter(OktaUserGroupMember.user_id == current_user.id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .count()
            > 0
        )

    @staticmethod
    def is_app_owner_group_owner(
        db: Session, current_user: OktaUser, app_group: Optional[AppGroup] = None, app: Optional[App] = None
    ) -> bool:
        """Check if the current user is an owner of the app owner group"""
        if app is not None:
            app_id = app.id
        elif app_group is not None and isinstance(app_group, AppGroup):
            app_id = app_group.app_id
        else:
            return False

        owner_app_groups = (
            db.query(AppGroup)
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(AppGroup.app_id == app_id)
            .filter(AppGroup.is_owner.is_(True))
            .all()
        )

        if not owner_app_groups:
            return False

        # Allow only app owner group owners to manage an app
        return (
            db.query(OktaUserGroupMember)
            .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups]))
            .filter(OktaUserGroupMember.user_id == current_user.id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .count()
            > 0
        )

    @staticmethod
    def is_access_admin(db: Session, current_user: OktaUser) -> bool:
        """Check if the current user is an Access admin (member of Access app owner group)"""
        access_app = (
            db.query(App)
            .options(selectinload(App.active_owner_app_groups))
            .filter(App.deleted_at.is_(None))
            .filter(App.name == App.ACCESS_APP_RESERVED_NAME)
            .first()
        )

        if access_app is None or not access_app.active_owner_app_groups:
            return False

        return (
            db.query(OktaUserGroupMember)
            .filter(OktaUserGroupMember.group_id.in_([ag.id for ag in access_app.active_owner_app_groups]))
            .filter(OktaUserGroupMember.user_id == current_user.id)
            .filter(OktaUserGroupMember.is_owner.is_(False))  # Members, not owners
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .count()
            > 0
        )

    @staticmethod
    def can_manage_group(group: OktaGroup, current_user: OktaUser, db: Session) -> bool:
        """Combination check to see if user can manage the group"""
        # Check if the current user is a group owner
        if AuthorizationHelpers.is_group_owner(db, group, current_user):
            return True

        # If this is an app group, check if the current user is a member or owner of the app owner group
        if isinstance(group, AppGroup) and AuthorizationHelpers.is_app_owner_group_owner(
            db, current_user, app_group=group
        ):
            return True

        # Finally check to see if they are an Access admin
        if AuthorizationHelpers.is_access_admin(db, current_user):
            return True

        return False


# Authorization dependency functions


async def require_access_admin(
    current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> OktaUser:
    """
    Dependency that requires the current user to be an Access admin.

    Returns:
        The current user if they are an Access admin

    Raises:
        HTTPException: If user is not an Access admin
    """
    if not AuthorizationHelpers.is_access_admin(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Current user is not allowed to perform this action"
        )
    return current_user


async def require_access_admin_or_app_creator(
    current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> OktaUser:
    """
    Dependency that requires the current user to be an Access admin or app creator.

    Returns:
        The current user if they are authorized

    Raises:
        HTTPException: If user is not authorized
    """
    # Check if the current user is an app creator
    app_creator_id = os.getenv("APP_CREATOR_ID")
    if app_creator_id and current_user.id == app_creator_id:
        return current_user

    # Check if they are an Access admin
    if AuthorizationHelpers.is_access_admin(db, current_user):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Current user is not allowed to perform this action"
    )


def require_group_management_permission(group_id: str):
    """
    Factory function that creates a dependency to check group management permissions.

    Args:
        group_id: The group ID to check permissions for

    Returns:
        A dependency function that validates permissions
    """

    async def check_permission(
        current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> tuple[OktaUser, OktaGroup]:
        """
        Check if current user can manage the specified group.

        Returns:
            Tuple of (current_user, group) if authorized

        Raises:
            HTTPException: If group not found or user not authorized
        """
        # Find the group (supporting polymorphic groups)
        group = (
            db.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
            .first()
        )

        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

        # Check if the current user can manage the group
        if not AuthorizationHelpers.can_manage_group(group, current_user, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Current user is not allowed to perform this action"
            )

        return current_user, group

    return check_permission


def require_app_management_permission(app_id: str):
    """
    Factory function that creates a dependency to check app management permissions.

    Args:
        app_id: The app ID to check permissions for

    Returns:
        A dependency function that validates permissions
    """

    async def check_permission(
        current_user: OktaUser = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> tuple[OktaUser, App]:
        """
        Check if current user can manage the specified app.

        Returns:
            Tuple of (current_user, app) if authorized

        Raises:
            HTTPException: If app not found or user not authorized
        """
        # Find the app
        app = db.query(App).filter(App.deleted_at.is_(None)).filter(or_(App.id == app_id, App.name == app_id)).first()

        if not app:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

        # Check if current user is app owner or Access admin
        if AuthorizationHelpers.is_app_owner_group_owner(
            db, current_user, app=app
        ) or AuthorizationHelpers.is_access_admin(db, current_user):
            return current_user, app

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Current user is not allowed to perform this action"
        )

    return check_permission
