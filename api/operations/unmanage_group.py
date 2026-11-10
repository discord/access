import logging

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectin_polymorphic

from api.context import get_request_context
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
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


# Run this operation when a group becomes unmanaged by Access
class UnmanageGroup:
    def __init__(self, *, group: OktaGroup | str, current_user_id: str | None = None):
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

        current_user = (
            await db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
            )
        ).first()
        current_user_id = getattr(current_user, "id", None)
        current_user_email = getattr(current_user, "email", None)

        if group.is_managed:
            return

        # The commits below expire the ORM rows, so the audit log is built
        # from plain-data snapshots taken before each update.
        audited_group = {"id": group.id, "name": group.name, "type": group.type}

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

        ended_access = []
        for active_access_via_role in (await db.session.scalars(active_access_via_roles_query)).all():
            ended_access.append(
                {
                    "user_id": active_access_via_role.user_id,
                    "group_id": active_access_via_role.group_id,
                    "is_owner": active_access_via_role.is_owner,
                }
            )
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

        ended_role_assignments = []
        for active_role_assignment in (await db.session.scalars(active_role_assignments_query)).all():
            ended_role_assignments.append(
                {
                    "role_group_id": active_role_assignment.role_group_id,
                    "group_id": active_role_assignment.group_id,
                    "is_owner": active_role_assignment.is_owner,
                }
            )
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

        if not dry_run:
            _ctx = get_request_context()
            logging.getLogger("access.audit").info(
                AuditLogSchema().dumps(
                    {
                        "event_type": EventType.group_unmanage,
                        "user_agent": _ctx.user_agent if _ctx else None,
                        "ip": _ctx.ip if _ctx else None,
                        "current_user_id": current_user_id,
                        "current_user_email": current_user_email,
                        "group": audited_group,
                        "memberships_ended_user_id_group_id": [a for a in ended_access if not a["is_owner"]],
                        "ownerships_ended_user_id_group_id": [a for a in ended_access if a["is_owner"]],
                        "role_memberships_ended_role_id_group_id": [
                            r for r in ended_role_assignments if not r["is_owner"]
                        ],
                        "role_ownerships_ended_role_id_group_id": [r for r in ended_role_assignments if r["is_owner"]],
                    }
                )
            )

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
                f"Rejecting obsolete access request {obsolete_access_request.id} for unmanaged group {group.id}"
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
