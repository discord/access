"""
Delete user operation for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

import asyncio
import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from api_v2.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser, OktaUserGroupMember
from api_v2.operations.reject_access_request import RejectAccessRequest
from api_v2.services import okta

logger = logging.getLogger(__name__)


class DeleteUser:
    def __init__(
        self,
        db: Session,
        *,
        user: OktaUser | str,
        sync_to_okta: bool = True,
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request

        if isinstance(user, str):
            self.user = self.db.query(OktaUser).filter(OktaUser.id == user).first()
        else:
            self.user = user

        self.sync_to_okta = sync_to_okta

        self.current_user_id = getattr(
            self.db.query(OktaUser)
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == current_user_id)
            .first(),
            "id",
            None,
        )

    def execute(self) -> None:
        # Run asynchronously to parallelize Okta API requests
        return asyncio.run(self._execute())

    async def _execute(self) -> None:
        # Create a list of okta asyncio tasks to wait to completion on at the end of this function
        okta_tasks = []

        if self.user.deleted_at is None:
            self.user.deleted_at = func.now()

        # End all user memberships including group memberships via a role
        group_access_query = (
            self.db.query(OktaUserGroupMember)
            .filter(
                or_(
                    OktaUserGroupMember.ended_at.is_(None),
                    OktaUserGroupMember.ended_at > func.now(),
                )
            )
            .filter(OktaUserGroupMember.user_id == self.user.id)
        )

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

        group_access_query.update({OktaUserGroupMember.ended_at: func.now()}, synchronize_session="fetch")

        self.db.commit()

        obsolete_access_requests = (
            self.db.query(AccessRequest)
            .filter(AccessRequest.requester_user_id == self.user.id)
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for obsolete_access_request in obsolete_access_requests:
            RejectAccessRequest(
                self.db,
                access_request=obsolete_access_request,
                rejection_reason="Closed because the requestor was deleted",
                current_user_id=self.current_user_id,
                request=self.request,
            ).execute()

        if len(okta_tasks) > 0:
            await asyncio.wait(okta_tasks)