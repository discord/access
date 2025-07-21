import random
import string
from datetime import datetime
from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    RoleGroup,
)
from api.models.app_group import get_access_owners, get_app_managers
from api.models.okta_group import get_group_managers
from api.operations.approve_access_request import ApproveAccessRequest
from api.operations.reject_access_request import RejectAccessRequest
from api.plugins import get_conditional_access_hook, get_notification_hook
from api.plugins.metrics_reporter import get_metrics_reporter_hook
from api.views.schemas import AuditLogSchema, EventType


class CreateAccessRequest:
    def __init__(
        self,
        *,
        requester_user: OktaUser | str,
        requested_group: OktaGroup | str,
        request_ownership: bool = False,
        request_reason: str = "",
        request_ending_at: Optional[datetime] = None,
    ):
        self.id = self.__generate_id()

        if isinstance(requester_user, str):
            self.requester = db.session.get(OktaUser, requester_user)
        else:
            self.requester = requester_user

        self.requested_group = (
            db.session.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == (requested_group if isinstance(requested_group, str) else requested_group.id))
            .first()
        )

        self.request_ownership = request_ownership
        self.request_reason = request_reason
        self.request_ending_at = request_ending_at

        self.request_approvers = db.session.query()

        self.conditional_access_hook = get_conditional_access_hook()
        self.notification_hook = get_notification_hook()
        self.metrics_hook = get_metrics_reporter_hook()

    def execute(self) -> Optional[AccessRequest]:
        # Don't allow creating a request for an unmanaged group
        if not self.requested_group.is_managed:
            return None

        access_request = AccessRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=self.requester.id,
            requested_group_id=self.requested_group.id,
            request_ownership=self.request_ownership,
            request_reason=self.request_reason,
            request_ending_at=self.request_ending_at,
        )

        db.session.add(access_request)
        db.session.commit()

        # Record metrics for access request creation
        group_type = "app_group" if isinstance(self.requested_group, AppGroup) else "role_group"
        self.metrics_hook.record_counter(
            metric_name="access.request.created",
            value=1.0,
            tags={
                "group_type": group_type,
                "request_ownership": str(self.request_ownership).lower(),
            },
        )

        # Fetch the users to notify
        approvers = get_group_managers(self.requested_group.id)

        # If there are no approvers, try to get the app managers
        # or if the only approver is the requester, try to get the app managers
        if (
            (len(approvers) == 0 and type(self.requested_group) is AppGroup)
            or (len(approvers) == 1 and approvers[0].id == self.requester.id)
            and type(self.requested_group) is AppGroup
        ):
            approvers = get_app_managers(self.requested_group.app_id)

        # If there are still no approvers, try to get the access owners
        if len(approvers) == 0 or (len(approvers) == 1 and approvers[0].id == self.requester.id):
            approvers = get_access_owners()

        group = (
            db.session.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                joinedload(AppGroup.app),
                selectinload(OktaGroup.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_app_tag_mapping), joinedload(OktaGroupTagMap.enabled_active_tag)
                ),
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == self.requested_group.id)
            .first()
        )

        # Audit logging
        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema(exclude=["request.resolution_reason", "request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.access_create,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.requester.id,
                    "current_user_email": self.requester.email,
                    "group": group,
                    "request": access_request,
                    "requester": self.requester,
                    "group_owners": approvers,
                }
            )
        )

        conditional_access_responses = self.conditional_access_hook.access_request_created(
            access_request=access_request,
            group=group,
            group_tags=[active_tag_map.enabled_active_tag for active_tag_map in group.active_group_tags],
            requester=self.requester,
        )

        for response in conditional_access_responses:
            if response is not None:
                if response.approved:
                    ApproveAccessRequest(
                        access_request=access_request,
                        approval_reason=response.reason,
                        ending_at=response.ending_at,
                        notify=False,
                    ).execute()
                else:
                    RejectAccessRequest(
                        access_request=access_request,
                        rejection_reason=response.reason,
                        notify=False,
                    ).execute()

                return access_request

        self.notification_hook.access_request_created(
            access_request=access_request,
            group=group,
            requester=self.requester,
            approvers=approvers,
        )

        return access_request

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
