from datetime import datetime
from typing import Optional

import logging

from api.context import get_request_context
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.exceptions import ConflictError, InvalidRequestError, ResourceGoneError

from api.extensions import db
from api.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations.constraints import CheckForReason
from api.operations.modify_group_users import ModifyGroupUsers
from api.plugins import get_notification_hook
from api.schemas import AuditLogSchema, EventType


class ApproveAccessRequest:
    def __init__(
        self,
        *,
        access_request: AccessRequest | str,
        approver_user: Optional[OktaUser | str] = None,
        approval_reason: str = "",
        ending_at: Optional[datetime] = None,
        notify: bool = True,
    ):
        self._access_request_arg = access_request
        self._approver_user_arg = approver_user

        self.approval_reason = approval_reason

        self.ending_at = ending_at

        self.notify = notify

        self.notification_hook = get_notification_hook()

    async def _resolve(self) -> None:
        access_request = self._access_request_arg
        approver_user = self._approver_user_arg

        # Lock the request row for the duration of the transaction so two
        # concurrent approvers can't both pass the pending-state guard below
        # and double-grant. `of=AccessRequest` keeps FOR UPDATE off the
        # joinedload's nullable outer-join side (Postgres rejects that); it's
        # a no-op on SQLite.
        request_result = await db.session.scalars(
            select(AccessRequest)
            .options(joinedload(AccessRequest.active_requested_group))
            .where(AccessRequest.id == (access_request if isinstance(access_request, str) else access_request.id))
            .with_for_update(of=AccessRequest)
        )
        self.access_request = request_result.first()

        if approver_user is None:
            self.approver_id = None
            self.approver_email = None
        elif isinstance(approver_user, str):
            approver = await db.session.get(OktaUser, approver_user)
            self.approver_id = approver.id
            self.approver_email = approver.email
        else:
            self.approver_id = approver_user.id
            self.approver_email = approver_user.email

    async def execute(self) -> AccessRequest:
        await self._resolve()
        # Don't allow approving a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent approval surfaces
        # as a conflict instead of looking like a success.
        if self.access_request.status != AccessRequestStatus.PENDING or self.access_request.resolved_at is not None:
            raise ConflictError("Access request is no longer pending")

        # Don't allow requester to approve their own request
        if self.access_request.requester_user_id == self.approver_id:
            return self.access_request

        # Don't allow approving a request if the reason is invalid and required
        valid, _ = await CheckForReason(
            group=self.access_request.requested_group,
            reason=self.approval_reason,
            members_to_add=[self.access_request.requester_user_id] if not self.access_request.request_ownership else [],
            owners_to_add=[self.access_request.requester_user_id] if self.access_request.request_ownership else [],
        ).execute_for_group()
        if not valid:
            return self.access_request

        # Don't allow approving a request if the requester is deleted
        requester = await db.session.get(OktaUser, self.access_request.requester_user_id)
        if requester is None or requester.deleted_at is not None:
            raise ResourceGoneError("The requester no longer exists")

        # Don't allow approving a request for a deleted or unmanaged group
        if self.access_request.active_requested_group is None:
            raise ResourceGoneError("The requested group no longer exists")
        if not self.access_request.active_requested_group.is_managed:
            raise InvalidRequestError("Groups not managed by Access cannot be modified")

        # Now handled inside ModifyGroupUsers
        # self.access_request.status = AccessRequestStatus.APPROVED
        # self.access_request.resolved_at = func.now()
        # self.access_request.resolver_user_id = self.approver_id
        # self.access_request.resolution_reason = self.approval_reason
        # self.access_request.approval_ending_at = self.ending_at

        # Audit logging
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.access_request.requested_group_id)
            )
        ).first()

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.access_approve,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.approver_id,
                    "current_user_email": self.approver_email,
                    "group": group,
                    "request": self.access_request,
                    "requester": await db.session.get(OktaUser, self.access_request.requester_user_id),
                }
            )
        )

        if self.access_request.request_ownership:
            await ModifyGroupUsers(
                group=self.access_request.requested_group_id,
                current_user_id=self.approver_id,
                users_added_ended_at=self.ending_at,
                created_reason=self.approval_reason,
                owners_to_add=[self.access_request.requester_user_id],
                notify=self.notify,
            ).execute()
        else:
            await ModifyGroupUsers(
                group=self.access_request.requested_group_id,
                current_user_id=self.approver_id,
                users_added_ended_at=self.ending_at,
                created_reason=self.approval_reason,
                members_to_add=[self.access_request.requester_user_id],
                notify=self.notify,
            ).execute()

        # Now handled inside ModifyGroupUsers
        # self.access_request.approved_membership_id = (
        #     db.session.query(OktaUserGroupMember).filter(
        #         OktaUserGroupMember.user_id == self.access_request.requester_user_id
        #     )
        #     .filter(
        #         OktaUserGroupMember.group_id == self.access_request.requested_group_id
        #     )
        #     .filter(OktaUserGroupMember.role_group_map_id.is_(None))
        #     .filter(
        #         OktaUserGroupMember.is_owner == self.access_request.request_ownership
        #     )
        #     .order_by(OktaUserGroupMember.created_at.desc())
        #     .first()
        #     .id
        # )
        # db.session.commit()

        # requester = db.session.get(OktaUser, self.access_request.requester_user_id)

        # approvers = get_all_possible_request_approvers(self.access_request)

        # self.notification_hook.access_request_completed(
        #     access_request=self.access_request,
        #     group=group,
        #     requester=requester,
        #     approvers=approvers,
        #     notify_requester=True,
        # )

        return self.access_request
