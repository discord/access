import asyncio
from typing import Optional

from sqlalchemy.orm import (
    joinedload,
)

from api.extensions import db
from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser, OktaUserGroupMember
from api.operations import RejectAccessRequest
from api.services import okta


class DeleteUser:
    def __init__(self, *, user: OktaUser | str, sync_to_okta: bool = True, current_user_id: Optional[str] = None):
        if isinstance(user, str):
            self.user = OktaUser.query.filter(OktaUser.id == user).first()
        else:
            self.user = user

        self.sync_to_okta = sync_to_okta

        self.current_user_id = getattr(
            OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first(),
            "id",
            None,
        )

    def execute(self) -> None:
        # Run asychronously to parallelize Okta API requests
        return asyncio.run(self._execute())

    async def _execute(self) -> None:
        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        okta_tasks = []

        if self.user.deleted_at is None:
            self.user.deleted_at = db.func.now()

        # End all user memberships including group memberships via a role
        group_access_query = OktaUserGroupMember.query.filter(
            db.or_(
                OktaUserGroupMember.ended_at.is_(None),
                OktaUserGroupMember.ended_at > db.func.now(),
            )
        ).filter(OktaUserGroupMember.user_id == self.user.id)

        if self.sync_to_okta:
            # Don't sync group access changes back to Okta for unmanaged groups
            managed_group_access_query = (
                group_access_query.options(joinedload(OktaUserGroupMember.group))
                .join(OktaUserGroupMember.group)
                .filter(OktaGroup.is_managed.is_(True))
            )
            # Remove user from group membership in Okta
            group_memberships_to_remove_ids = [
                m.group_id for m in managed_group_access_query.filter(OktaUserGroupMember.is_owner.is_(False)).all()
            ]

            for group_id in group_memberships_to_remove_ids:
                okta_tasks.append(asyncio.create_task(okta.async_remove_user_from_group(group_id, self.user.id)))

            # Remove user from group ownerships in Okta
            group_ownerships_to_remove_ids = [
                m.group_id for m in managed_group_access_query.filter(OktaUserGroupMember.is_owner.is_(True)).all()
            ]

            for group_id in group_ownerships_to_remove_ids:
                okta_tasks.append(asyncio.create_task(okta.async_remove_owner_from_group(group_id, self.user.id)))

        group_access_query.update({OktaUserGroupMember.ended_at: db.func.now()}, synchronize_session="fetch")

        db.session.commit()

        obsolete_access_requests = (
            AccessRequest.query.filter(AccessRequest.requester_user_id == self.user.id)
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for obsolete_access_request in obsolete_access_requests:
            RejectAccessRequest(
                access_request=obsolete_access_request,
                rejection_reason="Closed because the requestor was deleted",
                current_user_id=self.current_user_id,
            ).execute()

        if len(okta_tasks) > 0:
            await asyncio.wait(okta_tasks)
