import asyncio
from typing import Optional

from sqlalchemy.orm import (
    joinedload,
)

from sqlalchemy import func, or_, select, update
from api.extensions import db
from api.operations._fan_out import drain_fan_out_tasks
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    GroupRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleRequest,
)
from api.operations import RejectAccessRequest, RejectGroupRequest, RejectRoleRequest
from api.services import okta


class DeleteUser:
    def __init__(self, *, user: OktaUser | str, sync_to_okta: bool = True, current_user_id: Optional[str] = None):
        self.user_id = user if isinstance(user, str) else user.id

        self.sync_to_okta = sync_to_okta

        self.current_user_id = current_user_id

    async def execute(self) -> None:
        user = (await db.session.scalars(select(OktaUser).where(OktaUser.id == self.user_id))).first()

        current_user_id = getattr(
            (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == self.current_user_id)
                )
            ).first(),
            "id",
            None,
        )

        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        okta_tasks = []

        if user.deleted_at is None:
            user.deleted_at = func.now()

        # End all user memberships including group memberships via a role
        group_access_query = (
            select(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.user_id == user.id)
        )

        if self.sync_to_okta:
            # Don't sync group access changes back to Okta for unmanaged groups
            managed_group_access_query = (
                group_access_query.options(joinedload(OktaUserGroupMember.group))
                .join(OktaUserGroupMember.group)
                .where(OktaGroup.is_managed.is_(True))
            )
            # Remove user from group membership in Okta
            group_memberships_to_remove_ids = [
                m.group_id
                for m in (
                    await db.session.scalars(managed_group_access_query.where(OktaUserGroupMember.is_owner.is_(False)))
                ).all()
            ]

            for group_id in group_memberships_to_remove_ids:
                okta_tasks.append(asyncio.create_task(okta.remove_user_from_group(group_id, user.id)))

            # Remove user from group ownerships in Okta
            group_ownerships_to_remove_ids = [
                m.group_id
                for m in (
                    await db.session.scalars(managed_group_access_query.where(OktaUserGroupMember.is_owner.is_(True)))
                ).all()
            ]

            for group_id in group_ownerships_to_remove_ids:
                okta_tasks.append(asyncio.create_task(okta.remove_owner_from_group(group_id, user.id)))

        await db.session.execute(
            update(OktaUserGroupMember)
            .where(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .where(OktaUserGroupMember.user_id == user.id)
            .values({OktaUserGroupMember.ended_at: func.now()})
            .execution_options(synchronize_session="fetch")
        )

        await db.session.commit()

        obsolete_access_requests = (
            await db.session.scalars(
                select(AccessRequest)
                .where(AccessRequest.requester_user_id == user.id)
                .where(AccessRequest.status == AccessRequestStatus.PENDING)
                .where(AccessRequest.resolved_at.is_(None))
            )
        ).all()
        for obsolete_access_request in obsolete_access_requests:
            await RejectAccessRequest(
                access_request=obsolete_access_request,
                rejection_reason="Closed because the requestor was deleted",
                current_user_id=current_user_id,
            ).execute()

        # Reject pending role requests by the deleted user. ApproveRoleRequest
        # doesn't guard on a deleted requester, so a surviving one would still
        # grant the role access to the group after the requester is gone.
        obsolete_role_requests = (
            await db.session.scalars(
                select(RoleRequest)
                .where(RoleRequest.requester_user_id == user.id)
                .where(RoleRequest.status == AccessRequestStatus.PENDING)
                .where(RoleRequest.resolved_at.is_(None))
            )
        ).all()
        for obsolete_role_request in obsolete_role_requests:
            await RejectRoleRequest(
                role_request=obsolete_role_request,
                rejection_reason="Closed because the requestor was deleted",
                current_user_id=current_user_id,
            ).execute()

        # Reject pending group requests by the deleted user, mirroring above.
        obsolete_group_requests = (
            await db.session.scalars(
                select(GroupRequest)
                .where(GroupRequest.requester_user_id == user.id)
                .where(GroupRequest.status == AccessRequestStatus.PENDING)
                .where(GroupRequest.resolved_at.is_(None))
            )
        ).all()
        for obsolete_group_request in obsolete_group_requests:
            await RejectGroupRequest(
                group_request=obsolete_group_request,
                rejection_reason="Closed because the requestor was deleted",
                current_user_id=current_user_id,
            ).execute()

        await drain_fan_out_tasks(okta_tasks, f"DeleteUser for user {self.user_id}")
