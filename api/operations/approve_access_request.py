from datetime import datetime
from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.extensions import db
from api.models import AccessRequest, AccessRequestStatus, AppGroup, OktaGroup, OktaUser, RoleGroup
from api.operations.constraints import CheckForReason
from api.operations.modify_group_users import ModifyGroupUsers
from api.plugins import get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


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
        self.access_request = AccessRequest.query.options(
            joinedload(AccessRequest.active_requested_group)
        ).filter(
            AccessRequest.id == (access_request if isinstance(access_request, str) else access_request.id)
        ).first()

        if approver_user is None:
            self.approver_id = None
            self.approver_email = None
        elif isinstance(approver_user, str):
            approver = db.session.get(OktaUser, approver_user)
            self.approver_id = approver.id
            self.approver_email = approver.email
        else:
            self.approver_id = approver_user.id
            self.approver_email = approver_user.email

        self.approval_reason = approval_reason

        self.ending_at = ending_at

        self.notify = notify

        self.notification_hook = get_notification_hook()

    def execute(self) -> AccessRequest:
        # Don't allow approving a request that is already resolved
        if (
            self.access_request.status != AccessRequestStatus.PENDING
            or self.access_request.resolved_at is not None
        ):
            return self.access_request

        # Don't allow requester to approve their own request
        if self.access_request.requester_user_id == self.approver_id:
            return self.access_request

        # Don't allow approving a request if the reason is invalid and required
        valid, _ = CheckForReason(
            group=self.access_request.requested_group,
            reason=self.approval_reason,
            members_to_add=[self.access_request.requester_user_id] if not self.access_request.request_ownership else [],
            owners_to_add=[self.access_request.requester_user_id] if self.access_request.request_ownership else [],
        ).execute_for_group()
        if not valid:
            return self.access_request

        # Don't allow approving a request if the requester is deleted
        requester = db.session.get(OktaUser, self.access_request.requester_user_id)
        if requester is None or requester.deleted_at is not None:
            return self.access_request

        # Don't allow approving a request for an a deleted or unmanaged group
        if self.access_request.active_requested_group is None:
            return self.access_request
        if not self.access_request.active_requested_group.is_managed:
            return self.access_request

        # Now handled inside ModifyGroupUsers
        # self.access_request.status = AccessRequestStatus.APPROVED
        # self.access_request.resolved_at = db.func.now()
        # self.access_request.resolver_user_id = self.approver_id
        # self.access_request.resolution_reason = self.approval_reason
        # self.access_request.approval_ending_at = self.ending_at

        # Audit logging
        group = (db.session.query(OktaGroup)
                .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                         joinedload(AppGroup.app))
                .filter(OktaGroup.deleted_at.is_(None))
                .filter(OktaGroup.id == self.access_request.requested_group_id)
                .first())

        context = has_request_context()

        current_app.logger.info(AuditLogSchema().dumps({
            'event_type' : EventType.access_approve,
            'user_agent' : request.headers.get('User-Agent') if context else None,
            'ip' : request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
                        if context else None,
            'current_user_id' : self.approver_id,
            'current_user_email' : self.approver_email,
            'group' : group,
            'request' : self.access_request,
            'requester' : db.session.get(OktaUser, self.access_request.requester_user_id)
        }))

        if self.access_request.request_ownership:
            ModifyGroupUsers(
                group=self.access_request.requested_group_id,
                current_user_id=self.approver_id,
                users_added_ended_at=self.ending_at,
                created_reason=self.approval_reason,
                owners_to_add=[self.access_request.requester_user_id],
                notify=self.notify,
            ).execute()
        else:
            ModifyGroupUsers(
                group=self.access_request.requested_group_id,
                current_user_id=self.approver_id,
                users_added_ended_at=self.ending_at,
                created_reason=self.approval_reason,
                members_to_add=[self.access_request.requester_user_id],
                notify=self.notify,
            ).execute()

        # Now handled inside ModifyGroupUsers
        # self.access_request.approved_membership_id = (
        #     OktaUserGroupMember.query.filter(
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
        #     group=group.name,
        #     requester=requester,
        #     approvers=approvers,
        #     notify_requester=True,
        # )

        return self.access_request
