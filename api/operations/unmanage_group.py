
import logging
from typing import Optional

from sqlalchemy.orm import selectin_polymorphic

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.operations.reject_access_request import RejectAccessRequest

logger = logging.getLogger(__name__)

# Run this operation when a group becomes unmanaged by Access
class UnmanageGroup:
    def __init__(
        self,
        *,
        group: OktaGroup | str,
        current_user_id: Optional[str] = None
    ):
        self.group = (
            db.session.query(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .filter(OktaGroup.id == (group if isinstance(group, str) else group.id))
                .first()
            )

        self.current_user_id = (
            getattr(OktaUser.query
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id).first(), 'id', None)
        )


    def execute(self, dry_run: bool=False) -> None:
        if self.group.is_managed:
            return

        # TODO: End all direct ownerships of the group?

        # End all group memberships and ownerships via a role (not direct memberships or ownerships)
        active_access_via_roles_query = OktaUserGroupMember.query.filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        ).filter(OktaUserGroupMember.group_id == self.group.id
        ).filter(OktaUserGroupMember.role_group_map_id.isnot(None))

        for active_access_via_role in active_access_via_roles_query.all():
            logger.info(
                f"User {active_access_via_role.user_id} has invalid " \
                f"{'ownership' if active_access_via_role.is_owner else 'membership'} " \
                f"to unmanaged group {self.group.id} via role"
            )

        if not dry_run:
            active_access_via_roles_query.update(
                {OktaUserGroupMember.ended_at: db.func.now()}, synchronize_session="fetch"
            )
            db.session.commit()

        # End all roles associations where this group was a member
        active_role_assignments_query = RoleGroupMap.query.filter(
            db.or_(
                RoleGroupMap.ended_at.is_(None),
                RoleGroupMap.ended_at > db.func.now()
            )
        ).filter(RoleGroupMap.group_id == self.group.id)

        for active_role_assignment in active_role_assignments_query.all():
            logger.info(
                f"Role {active_role_assignment.role_group_id} has invalid " \
                f"{'ownership' if active_role_assignment.is_owner else 'membership'} " \
                f"to unmanaged group {self.group.id}"
            )

        if not dry_run:
            active_role_assignments_query.update(
                {RoleGroupMap.ended_at: db.func.now()}, synchronize_session="fetch"
            )
            db.session.commit()

        # If this groups is a RoleGroup, do not remove the groups associated with the role
        # Unmanaged roles can still be associated with groups


        # Reject all pending access requests for this group
        obsolete_access_requests = (
            AccessRequest.query.filter(
                AccessRequest.requested_group_id == self.group.id
            )
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for obsolete_access_request in obsolete_access_requests:
            logger.info(
                f"Rejecting obsolete access request {obsolete_access_request.id} " \
                f"for unmanaged group {self.group.id}"
            )
            if not dry_run:
                RejectAccessRequest(
                    access_request=obsolete_access_request,
                    rejection_reason="Closed because the requested group is no longer managed by Access",
                    current_user_id=self.current_user_id
                ).execute()
