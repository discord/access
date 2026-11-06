"""Shared helpers for the deletion request operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import joinedload, with_polymorphic

from api.exceptions import InvalidRequestError, ResourceGoneError
from api.extensions import db
from api.models import App, AppGroup, DeletionRequest, OktaGroup, OktaUser, RoleGroup
from api.models.app_group import get_access_owners, get_app_managers
from api.models.okta_group import get_group_managers


async def load_deletable_target(
    *, group_id: str | None = None, app_id: str | None = None
) -> tuple[OktaGroup | None, App | None]:
    """Load the active group or app a deletion request targets and check it may be deleted.

    Raises:
        ResourceGoneError: The target doesn't exist or was deleted.
        InvalidRequestError: The target can't be deleted: an unmanaged group, an app's
            owners group, or the Access app.
    """
    if group_id is not None:
        poly_group = with_polymorphic(OktaGroup, [AppGroup, RoleGroup])
        group = (
            await db.session.scalars(
                select(poly_group)
                .options(joinedload(poly_group.AppGroup.app))
                .where(poly_group.id == group_id)
                .where(poly_group.deleted_at.is_(None))
            )
        ).first()
        if group is None:
            raise ResourceGoneError("Group not found")
        if not group.is_managed:
            raise InvalidRequestError("Groups not managed by Access cannot be modified")
        if type(group) is AppGroup and group.is_owner:
            raise InvalidRequestError(
                "Application owner groups cannot be deleted without first deleting the application"
            )
        return group, None
    app = (await db.session.scalars(select(App).where(App.id == app_id).where(App.deleted_at.is_(None)))).first()
    if app is None:
        raise ResourceGoneError("App not found")
    if app.name == App.ACCESS_APP_RESERVED_NAME:
        raise InvalidRequestError("The Access Application cannot be deleted")
    return None, app


async def approvers_for(group: OktaGroup | None, app: App | None) -> list[OktaUser]:
    """Who to notify of a deletion request: the group's owners, then its app's owners, then Access admins."""
    approvers: list[OktaUser] = []
    if group is not None:
        approvers = await get_group_managers(group.id)
        if not approvers and type(group) is AppGroup:
            approvers = await get_app_managers(group.app_id)
    elif app is not None:
        approvers = await get_app_managers(app.id)
    return approvers or await get_access_owners()


async def lock_pending(deletion_request_id: str) -> DeletionRequest | None:
    """Lock and return the deletion request, or None if it doesn't exist."""
    return (
        await db.session.scalars(
            select(DeletionRequest).where(DeletionRequest.id == deletion_request_id).with_for_update()
        )
    ).first()
