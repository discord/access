import logging
from typing import Optional

from sqlalchemy.orm import selectin_polymorphic

from sqlalchemy import func, or_, select, update
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
    RoleRequest,
)
from api.operations.reject_access_request import RejectAccessRequest
from api.operations.reject_role_request import RejectRoleRequest

logger = logging.getLogger(__name__)


# Run this operation when a group becomes unmanaged by Access
class UnmanageGroup:
    def __init__(self, *, group: OktaGroup | str, current_user_id: Optional[str] = None):
        self.group_id = group if isinstance(group, str) else group.id
        self.current_user_id = current_user_id

    async def execute(self, dry_run: bool = False) -> None:
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(OktaGroup.id == self.group_id)
            )
        ).first()
        assert group is not None

        current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first(),
            "id",
            None,
        )

        if group.is_managed:
            return

        # TODO: End all direct ownerships of the group?

        # End all group memberships and ownerships via a role (not direct memberships or ownerships)
        active_access_via_roles_query = (
            select(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.group_id == group.id)
            .where(OktaUserGroupMember.role_group_map_id.isnot(None))
        )

        for active_access_via_role in (await db.session.scalars(active_access_via_roles_query)).all():
            logger.info(
                f"User {active_access_via_role.user_id} has invalid "
                f"{'ownership' if active_access_via_role.is_owner else 'membership'} "
                f"to unmanaged group {group.id} via role"
            )

        if not dry_run:
            await db.session.execute(
                update(OktaUserGroupMember)
                .where(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
                    )
                )
                .where(OktaUserGroupMember.group_id == group.id)
                .where(OktaUserGroupMember.role_group_map_id.isnot(None))
                .values({OktaUserGroupMember.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )
            await db.session.commit()

        # End all roles associations where this group was a member
        active_role_assignments_query = (
            select(RoleGroupMap)
            .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
            .where(RoleGroupMap.group_id == group.id)
        )

        for active_role_assignment in (await db.session.scalars(active_role_assignments_query)).all():
            logger.info(
                f"Role {active_role_assignment.role_group_id} has invalid "
                f"{'ownership' if active_role_assignment.is_owner else 'membership'} "
                f"to unmanaged group {group.id}"
            )

        if not dry_run:
            await db.session.execute(
                update(RoleGroupMap)
                .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
                .where(RoleGroupMap.group_id == group.id)
                .values({RoleGroupMap.ended_at: func.now()})
                .execution_options(synchronize_session="fetch")
            )
            await db.session.commit()

        # If this groups is a RoleGroup, do not remove the groups associated with the role
        # Unmanaged roles can still be associated with groups

        # Reject all pending access requests for this group
        obsolete_access_requests = (
            await db.session.scalars(
                select(AccessRequest)
                .where(AccessRequest.requested_group_id == group.id)
                .where(AccessRequest.status == AccessRequestStatus.PENDING)
                .where(AccessRequest.resolved_at.is_(None))
            )
        ).all()
        for obsolete_access_request in obsolete_access_requests:
            logger.info(
                f"Rejecting obsolete access request {obsolete_access_request.id} " f"for unmanaged group {group.id}"
            )
            if not dry_run:
                await RejectAccessRequest(
                    access_request=obsolete_access_request,
                    rejection_reason="Closed because the requested group is no longer managed by Access",
                    current_user_id=current_user_id,
                ).execute()

        # Reject all pending role requests touching this group, either as the
        # requested target or as the requester role.
        obsolete_role_requests = (
            await db.session.scalars(
                select(RoleRequest)
                .where(
                    or_(
                        RoleRequest.requested_group_id == group.id,
                        RoleRequest.requester_role_id == group.id,
                    )
                )
                .where(RoleRequest.status == AccessRequestStatus.PENDING)
                .where(RoleRequest.resolved_at.is_(None))
            )
        ).all()
        for obsolete_role_request in obsolete_role_requests:
            logger.info(f"Rejecting obsolete role request {obsolete_role_request.id} for unmanaged group {group.id}")
            if not dry_run:
                await RejectRoleRequest(
                    role_request=obsolete_role_request,
                    rejection_reason="Closed because a group in this role request is no longer managed by Access",
                    current_user_id=current_user_id,
                ).execute()
