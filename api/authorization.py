from functools import wraps
from typing import Any, Callable, Optional, ParamSpec, TypeVar

from flask import abort, current_app, g
from sqlalchemy.orm import (
    selectinload,
    with_polymorphic,
)

from api.extensions import db
from api.models import (
    App,
    AppGroup,
    OktaGroup,
    OktaUserGroupMember,
    RoleGroup,
)

R = TypeVar("R")
P = ParamSpec("P")

class AuthorizationDecorator:
    @staticmethod
    def require_app_or_group_owner_or_access_admin_for_group(
        view_func: Callable[P, R]) -> Callable[[Any, Optional[str], Any], R]:
        @wraps(view_func)
        def decorated(*args: P.args, group_id: Optional[str] = None, **kwargs: P.kwargs) -> R:
            group = (
                db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(OktaGroup.deleted_at.is_(None))
                .filter(db.or_(OktaGroup.id == group_id, OktaGroup.name == group_id))
                .first_or_404()
            )
            # Check if the current user can manage the group
            if AuthorizationHelpers.can_manage_group(group):
                return view_func(*args, group=group, **kwargs)

            return abort(403, "Current user is not allowed to perform this action")

        return decorated

    @staticmethod
    def require_app_owner_or_access_admin_for_app(
        view_func: Callable[P, R]) -> Callable[[Any, Optional[str], Any], R]:
        @wraps(view_func)
        def decorated(*args: P.args, app_id: Optional[str] = None, **kwargs: P.kwargs) -> R:
            app = (
                App.query.filter(App.deleted_at.is_(None))
                .filter(db.or_(App.id == app_id, App.name == app_id))
                .first_or_404()
            )
            # If this is an app group, check if the current user is a member or owner of the app owner group
            if AuthorizationHelpers.is_app_owner_group_owner(app=app):
                return view_func(*args, app=app, **kwargs)

            # Finally check to see if they are an Access owner (aka. Access admin)
            if AuthorizationHelpers.is_access_admin():
                return view_func(*args, app=app, **kwargs)

            return abort(403, "Current user is not allowed to perform this action")

        return decorated

        # Check if the current user is a group owner

    @staticmethod
    def require_access_admin_or_app_creator(view_func: Callable[P, R]) -> Callable[P, R]:
        @wraps(view_func)
        def decorated(*args: P.args, **kwargs: P.kwargs) -> R:
            # Check if the current user is an app creator
            if current_app.config["APP_CREATOR_ID"] is not None and g.current_user_id == current_app.config["APP_CREATOR_ID"]:
                return view_func(*args, **kwargs)
            # Finally check to see if they are an Access owner (aka. Access admin)
            if AuthorizationHelpers.is_access_admin():
                return view_func(*args, **kwargs)

            return abort(403, "Current user is not allowed to perform this action")

        return decorated

    @staticmethod
    def require_access_admin(view_func: Callable[P, R]) -> Callable[P, R]:
        @wraps(view_func)
        def decorated(*args: P.args, **kwargs: P.kwargs) -> R:
            # Check to see if they are an Access owner (aka. Access admin)
            if AuthorizationHelpers.is_access_admin():
                return view_func(*args, **kwargs)

            return abort(403, "Current user is not allowed to perform this action")

        return decorated


class AuthorizationHelpers:
    # Check if the current user is a owner of the group
    @staticmethod
    def is_group_owner(group: OktaGroup) -> bool:
        return (
            OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == group.id)
            .filter(OktaUserGroupMember.user_id == g.current_user_id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .count()
            > 0
        )

    # If this is an app group, check if the current user is a owner of the app owner group
    @staticmethod
    def is_app_owner_group_owner(app_group: Optional[AppGroup] = None, app: Optional[App] = None) -> bool:
        if app is not None:
            app_id = app.id
        elif app_group is not None and type(app_group) == AppGroup:
            app_id = app_group.app_id
        else:
            return False

        owner_app_groups = (
            AppGroup.query.filter(OktaGroup.deleted_at.is_(None))
            .filter(AppGroup.app_id == app_id)
            .filter(AppGroup.is_owner.is_(True))
        )
        if owner_app_groups.count() == 0:
            return False

        # Allow only app owner group owners to manage an app
        return (
            OktaUserGroupMember.query.filter(
                OktaUserGroupMember.group_id.in_([ag.id for ag in owner_app_groups])
            )
            .filter(OktaUserGroupMember.user_id == g.current_user_id)
            .filter(OktaUserGroupMember.is_owner.is_(True))
            .filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .count()
            > 0
        )

    # Check to see if they are an Access app owner group member (aka. Access admin)
    @staticmethod
    def is_access_admin(current_user_id: Optional[str] = None) -> bool:
        if current_user_id is None:
            current_user_id = g.current_user_id
        
        access_app = (
            App.query.options(selectinload(App.active_owner_app_groups))
            .filter(App.deleted_at.is_(None))
            .filter(App.name == App.ACCESS_APP_RESERVED_NAME)
            .first()
        )
        if access_app is None:
            return False

        if len(access_app.active_owner_app_groups) == 0:
            return False

        return (
            OktaUserGroupMember.query.filter(
                OktaUserGroupMember.group_id.in_(
                    [ag.id for ag in access_app.active_owner_app_groups]
                )
            )
            .filter(OktaUserGroupMember.user_id == current_user_id)
            .filter(OktaUserGroupMember.is_owner.is_(False))
            .filter(
                db.or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > db.func.now(),
                )
            )
            .count()
            > 0
        )

    # Combination of all the above helper methods
    @staticmethod
    def can_manage_group(group: OktaGroup) -> bool:
        # Check if the current user is a group owner
        if AuthorizationHelpers.is_group_owner(group):
            return True

        # If this is an app group, check if the current user is a member or owner of the app owner group
        if AuthorizationHelpers.is_app_owner_group_owner(app_group=group):
            return True

        # Finally check to see if they are an Access owner (aka. Access admin)
        if AuthorizationHelpers.is_access_admin():
            return True
        return False
