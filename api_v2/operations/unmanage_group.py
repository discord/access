"""
Unmanage group operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectin_polymorphic

from api_v2.models import (
    AccessRequest,
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api_v2.operations.reject_access_request import RejectAccessRequest

logger = logging.getLogger(__name__)


# Run this operation when a group becomes unmanaged by Access
class UnmanageGroup:
    def __init__(
        self,
        db: Session,
        *,
        group: OktaGroup | str,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        self.group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
            .first()
        )

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def execute(self, dry_run: bool = False) -> None:
        if self.group.is_managed:
            return

        # TODO: End all direct ownerships of the group?

        # End all group memberships and ownerships via a role (not direct memberships or ownerships)
        active_access_via_roles_query = (
            self.db.query(OktaUserGroupMember)
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .filter(OktaUserGroupMember.group_id == self.group.id)
            .filter(OktaUserGroupMember.role_group_map_id.isnot(None))
        )

        for active_access_via_role in active_access_via_roles_query.all():
            logger.info(
                f"User {active_access_via_role.user_id} has invalid "
                f"{'ownership' if active_access_via_role.is_owner else 'membership'} "
                f"to unmanaged group {self.group.id} via role"
            )

        if not dry_run:
            active_access_via_roles_query.update(
                {OktaUserGroupMember.ended_at: func.now()}, synchronize_session="fetch"
            )
            self.db.commit()

        # End all roles associations where this group was a member
        active_role_assignments_query = (
            self.db.query(RoleGroupMap)
            .filter(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
            .filter(RoleGroupMap.group_id == self.group.id)
        )

        for active_role_assignment in active_role_assignments_query.all():
            logger.info(
                f"Role {active_role_assignment.role_group_id} has invalid "
                f"{'ownership' if active_role_assignment.is_owner else 'membership'} "
                f"to unmanaged group {self.group.id}"
            )

        if not dry_run:
            active_role_assignments_query.update({RoleGroupMap.ended_at: func.now()}, synchronize_session="fetch")
            self.db.commit()

        # If this groups is a RoleGroup, do not remove the groups associated with the role
        # Unmanaged roles can still be associated with groups

        # Reject all pending access requests for this group
        obsolete_access_requests = (
            self.db.query(AccessRequest)
            .filter(AccessRequest.requested_group_id == self.group.id)
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for obsolete_access_request in obsolete_access_requests:
            logger.info(
                f"Rejecting obsolete access request {obsolete_access_request.id} "
                f"for unmanaged group {self.group.id}"
            )
            if not dry_run:
                RejectAccessRequest(
                    self.db,
                    access_request=obsolete_access_request,
                    rejection_reason="Closed because the requested group is no longer managed by Access",
                    current_user_id=self.current_user_id,
                    request=self.request,
                ).execute()