from typing import Optional

import logging

from sqlalchemy import func, select
from api.context import get_request_context

from api.exceptions import ConflictError
from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, GroupRequest, OktaUser, OktaUserGroupMember
from api.models.access_request import get_all_possible_request_approvers
from api.models.app_group import get_access_owners
from api.plugins import get_notification_hook
from api.schemas import AuditLogSchema, EventType


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
        self._group_request_arg = group_request
        self._current_user_id_arg = current_user_id

        self.rejection_reason = rejection_reason
        self.notify = notify
        self.notify_requester = notify_requester

        self.notification_hook = get_notification_hook()

    def _resolve(self) -> None:
        group_request = self._group_request_arg
        current_user_id = self._current_user_id_arg

        # Lock the request row so a reject can't race a concurrent approve/
        # reject; both serialize on this row and the loser hits the resolved
        # guard. No-op on SQLite.
        request_id = group_request if isinstance(group_request, str) else group_request.id
        self.group_request = db.session.scalars(
            select(GroupRequest).where(GroupRequest.id == request_id).with_for_update()
        ).first()

        if current_user_id is None:
            self.rejecter_id = None
        elif isinstance(current_user_id, str):
            user = db.session.scalars(
                select(OktaUser).where(OktaUser.deleted_at.is_(None)).where(OktaUser.id == current_user_id)
            ).first()
            self.rejecter_id = user.id if user else None
        else:
            self.rejecter_id = current_user_id.id

    def execute(self) -> GroupRequest:
        self._resolve()
        # Already resolved — raise rather than silently no-op so a stale/
        # concurrent rejection surfaces as a conflict instead of a success.
        if self.group_request.status != AccessRequestStatus.PENDING or self.group_request.resolved_at is not None:
            raise ConflictError("Group request is no longer pending")

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
                        is_app_owner = db.session.scalars(
                            select(OktaUserGroupMember)
                            .join(AppGroup, OktaUserGroupMember.group_id == AppGroup.id)
                            .where(
                                AppGroup.app_id == resolved_app_id,
                                AppGroup.is_owner.is_(True),
                                AppGroup.deleted_at.is_(None),
                                OktaUserGroupMember.user_id == self.rejecter_id,
                                OktaUserGroupMember.is_owner.is_(True),
                                OktaUserGroupMember.ended_at.is_(None),
                            )
                        ).first()

                        if not is_app_owner:
                            return self.group_request
                    else:
                        # Non-app-group request: only global admins can reject
                        return self.group_request

        # Audit logging
        email = getattr(db.session.get(OktaUser, self.rejecter_id), "email", None) if self.rejecter_id else None
        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_request_reject,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": self.rejecter_id,
                    "current_user_email": email,
                    "group_request": self.group_request,
                    "requester": db.session.get(OktaUser, self.group_request.requester_user_id),
                }
            )
        )

        self.group_request.status = AccessRequestStatus.REJECTED
        self.group_request.resolved_at = func.now()
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
