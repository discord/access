from typing import Optional

from flask import current_app, has_request_context, request

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, GroupRequest, OktaUser, OktaUserGroupMember
from api.models.access_request import get_all_possible_request_approvers
from api.models.app_group import get_access_owners
from api.plugins import get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class RejectGroupRequest:
    def __init__(
        self,
        *,
        group_request: GroupRequest | str,
        rejection_reason: str = "",
        notify: bool = True,
        notify_requester: bool = True,
        current_user_id: Optional[str | OktaUser] = None,
    ):
        if isinstance(group_request, str):
            self.group_request = db.session.get(GroupRequest, group_request)
        else:
            self.group_request = group_request

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            user = OktaUser.query.filter(OktaUser.deleted_at.is_(None)).filter(OktaUser.id == current_user_id).first()
            self.rejecter_id = user.id if user else None
        else:
            self.rejecter_id = current_user_id.id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def execute(self) -> GroupRequest:
        # Already resolved
        if self.group_request.status != AccessRequestStatus.PENDING or self.group_request.resolved_at is not None:
            return self.group_request

        resolved_app_id = (
            self.group_request.resolved_app_id
            if self.group_request.resolved_app_id
            else self.group_request.requested_app_id
        )

        if self.rejecter_id is not None:
            is_self_rejection = self.group_request.requester_user_id == self.rejecter_id

            if not is_self_rejection:
                access_owner_ids = {u.id for u in get_access_owners()}
                is_global_admin = self.rejecter_id in access_owner_ids

                if not is_global_admin:
                    # Check app ownership if this is an app group request
                    if resolved_app_id is not None:
                        is_app_owner = (
                            db.session.query(OktaUserGroupMember)
                            .join(AppGroup, OktaUserGroupMember.group_id == AppGroup.id)
                            .filter(
                                AppGroup.app_id == resolved_app_id,
                                AppGroup.is_owner.is_(True),
                                AppGroup.deleted_at.is_(None),
                                OktaUserGroupMember.user_id == self.rejecter_id,
                                OktaUserGroupMember.ended_at.is_(None),
                            )
                            .first()
                        )

                        if not is_app_owner:
                            return self.group_request
                    else:
                        # Non-app-group request: only global admins can reject
                        return self.group_request

        # Audit logging
        email = getattr(db.session.get(OktaUser, self.rejecter_id), "email", None) if self.rejecter_id else None
        context = has_request_context()
        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_request_reject,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.rejecter_id,
                    "current_user_email": email,
                    "group_request": self.group_request,
                    "requester": db.session.get(OktaUser, self.group_request.requester_user_id),
                }
            )
        )

        self.group_request.status = AccessRequestStatus.REJECTED
        self.group_request.resolved_at = db.func.now()
        self.group_request.resolver_user_id = self.rejecter_id
        self.group_request.resolution_reason = self.rejection_reason

        db.session.commit()

        if self.notify:
            requester = db.session.get(OktaUser, self.group_request.requester_user_id)

            approvers = get_all_possible_request_approvers(self.group_request)

            self.notification_hook.access_group_request_completed(
                group_request=self.group_request,
                group=None,
                requester=requester,
                approvers=approvers,
                notify_requester=self.notify_requester,
            )

        return self.group_request
