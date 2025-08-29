import logging
import random
import string
from datetime import datetime
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic, selectinload

from api_v2.models import (
    AccessRequest,
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    RoleGroup,
)
from api_v2.models.app_group import get_access_owners, get_app_managers
from api_v2.models.okta_group import get_group_managers
from api_v2.operations.approve_access_request import ApproveAccessRequest
from api_v2.operations.reject_access_request import RejectAccessRequest
from api_v2.plugins import get_conditional_access_hook, get_notification_hook
from api_v2.schemas import (
    AuditAccessRequestSummary,
    AuditAppSummary,
    AuditEventType,
    AuditGroupSummary,
    AuditLogRead,
    AuditUserSummary,
)

logger = logging.getLogger(__name__)


class CreateAccessRequest:
    def __init__(
        self,
        db: Session,
        *,
        requester_user: OktaUser | str,
        requested_group: OktaGroup | str,
        request_ownership: bool = False,
        request_reason: str = "",
        request_ending_at: Optional[datetime] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request
        self.id = self._generate_id()

        if isinstance(requester_user, str):
            self.requester = self.db.get(OktaUser, requester_user)
        else:
            self.requester = requester_user

        self.requested_group = (
            self.db.query(OktaGroup)
            .options(selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]), joinedload(AppGroup.app))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == (requested_group if isinstance(requested_group, str) else requested_group.id))
            .first()
        )

        self.request_ownership = request_ownership
        self.request_reason = request_reason
        self.request_ending_at = request_ending_at

        self.conditional_access_hook = get_conditional_access_hook()
        self.notification_hook = get_notification_hook()

    def _log_audit_event(self, access_request: AccessRequest, group: OktaGroup, approvers: list[OktaUser]) -> None:
        """Log audit event for access request creation."""
        # Build audit data
        audit_data = {
            "event_type": AuditEventType.ACCESS_CREATE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.requester.id,
            "current_user_email": self.requester.email,
            "group": AuditGroupSummary(
                id=group.id,
                name=group.name,
                type=group.type,
                app=AuditAppSummary(id=group.app.id, name=group.app.name)
                if hasattr(group, "app") and group.app
                else None,
            ),
            "request": AuditAccessRequestSummary(
                id=access_request.id,
                request_reason=access_request.request_reason,
                request_ending_at=access_request.request_ending_at,
                request_ownership=access_request.request_ownership,
            ),
            "requester": AuditUserSummary(
                id=self.requester.id,
                email=self.requester.email,
                first_name=self.requester.first_name,
                last_name=self.requester.last_name,
                display_name=self.requester.display_name,
            ),
            "group_owners": [
                AuditUserSummary(
                    id=approver.id,
                    email=approver.email,
                    first_name=approver.first_name,
                    last_name=approver.last_name,
                    display_name=approver.display_name,
                )
                for approver in approvers
            ],
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For")
                or self.request.headers.get("X-Real-IP")
                or self.request.client.host
                if self.request.client
                else None
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

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

        self.db.add(access_request)
        self.db.commit()

        # Fetch the users to notify
        approvers = get_group_managers(self.db, self.requested_group.id)

        # If there are no approvers, try to get the app managers
        # or if the only approver is the requester, try to get the app managers
        if (
            (len(approvers) == 0 and type(self.requested_group) is AppGroup)
            or (len(approvers) == 1 and approvers[0].id == self.requester.id)
            and type(self.requested_group) is AppGroup
        ):
            approvers = get_app_managers(self.db, self.requested_group.app_id)

        # If there are still no approvers, try to get the access owners
        if len(approvers) == 0 or (len(approvers) == 1 and approvers[0].id == self.requester.id):
            approvers = get_access_owners(self.db)

        group = (
            self.db.query(OktaGroup)
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
        self._log_audit_event(access_request, group, approvers)

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
                        self.db,
                        access_request=access_request,
                        approval_reason=response.reason,
                        ending_at=response.ending_at,
                        notify=False,
                        request=self.request,
                    ).execute()
                else:
                    RejectAccessRequest(
                        self.db,
                        access_request=access_request,
                        rejection_reason=response.reason,
                        notify=False,
                        request=self.request,
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
    def _generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
