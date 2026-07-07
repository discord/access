from datetime import datetime
from typing import Optional

import logging

from api.context import get_request_context
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.exceptions import ConflictError, InvalidRequestError, ResourceGoneError

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.operations.constraints import CheckForReason
from api.operations.modify_role_groups import ModifyRoleGroups
from api.schemas import AuditLogSchema, EventType


class ApproveRoleRequest:
    def __init__(
        self,
        *,
        role_request: RoleRequest | str,
        approver_user: Optional[OktaUser | str] = None,
        approval_reason: str = "",
        ending_at: Optional[datetime] = None,
        notify: bool = True,
    ):
        self.role_request_id = role_request if isinstance(role_request, str) else role_request.id
        self.approver_user_id = (
            approver_user.id if approver_user is not None and not isinstance(approver_user, str) else approver_user
        )

        self.approval_reason = approval_reason

        self.ending_at = ending_at

        self.notify = notify

    async def execute(self) -> RoleRequest:
        # Lock the request row for the transaction so concurrent approvers
        # can't both pass the pending-state guard and double-grant. `of=` keeps
        # FOR UPDATE off the joinedloads' nullable outer-join sides (Postgres
        # rejects that); no-op on SQLite.
        role_request = (
            await db.session.scalars(
                select(RoleRequest)
                .options(joinedload(RoleRequest.active_requested_group), joinedload(RoleRequest.active_requester_role))
                .where(RoleRequest.id == self.role_request_id)
                .with_for_update(of=RoleRequest)
            )
        ).first()

        if self.approver_user_id is None:
            approver_id = None
            approver_email = None
        else:
            approver = await db.session.get(OktaUser, self.approver_user_id)
            approver_id = approver.id
            approver_email = approver.email

        # Don't allow approving a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent approval surfaces
        # as a conflict instead of looking like a success.
        if role_request.status != AccessRequestStatus.PENDING or role_request.resolved_at is not None:
            raise ConflictError("Role request is no longer pending")

        # Don't allow requester to approve their own request
        if role_request.requester_user_id == approver_id:
            return role_request

        # Don't allow approving a request if the reason is invalid and required
        valid, _ = await CheckForReason(
            group=role_request.requester_role_id,
            reason=self.approval_reason,
            members_to_add=[role_request.requested_group_id] if not role_request.request_ownership else [],
            owners_to_add=[role_request.requested_group_id] if role_request.request_ownership else [],
        ).execute_for_role()
        if not valid:
            return role_request

        # Don't allow approving a request if the requester role is deleted
        requester = await db.session.get(RoleGroup, role_request.requester_role_id)
        if requester is None or requester.deleted_at is not None:
            raise ResourceGoneError("The requester role no longer exists")

        # Don't allow approving a request for a deleted or unmanaged group
        if role_request.active_requested_group is None:
            raise ResourceGoneError("The requested group no longer exists")
        if not role_request.active_requested_group.is_managed:
            raise InvalidRequestError("Groups not managed by Access cannot be modified")

        await db.session.commit()

        # Audit logging
        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup]), joinedload(AppGroup.app))
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == role_request.requested_group_id)
            )
        ).first()

        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.role_request_approve,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": approver_id,
                    "current_user_email": approver_email,
                    "group": group,
                    "role_request": role_request,
                    "requester": await db.session.get(OktaUser, role_request.requester_user_id),
                }
            )
        )

        if role_request.request_ownership:
            await ModifyRoleGroups(
                role_group=role_request.requester_role,
                groups_added_ended_at=self.ending_at,
                owner_groups_to_add=[role_request.requested_group_id],
                current_user_id=approver_id,
                created_reason=self.approval_reason,
                notify=self.notify,
            ).execute()
        else:
            await ModifyRoleGroups(
                role_group=role_request.requester_role,
                groups_added_ended_at=self.ending_at,
                groups_to_add=[role_request.requested_group_id],
                current_user_id=approver_id,
                created_reason=self.approval_reason,
                notify=self.notify,
            ).execute()

        return role_request
