import random
import string
from datetime import datetime
from typing import List, Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    GroupRequest,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    RoleGroup,
    Tag,
)
from api.models.app_group import get_access_owners, get_app_managers
from api.models.tag import coalesce_ended_at
from api.operations.approve_group_request import ApproveGroupRequest
from api.operations.reject_group_request import RejectGroupRequest
from api.plugins import get_conditional_access_hook, get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class CreateGroupRequest:
    def __init__(
        self,
        *,
        requester_user: OktaUser | str,
        requested_group_name: str,
        requested_group_description: str = "",
        requested_group_type: str,
        requested_app_id: Optional[str] = None,
        requested_group_tags: List[str] = None,
        requested_ownership_ending_at: Optional[datetime] = None,
        request_reason: str = "",
    ):
        self.id = self.__generate_id()

        if isinstance(requester_user, str):
            self.requester = db.session.get(OktaUser, requester_user)
        else:
            self.requester = requester_user

        self.requested_group_name = requested_group_name
        self.requested_group_description = requested_group_description
        self.requested_group_type = requested_group_type
        self.requested_app_id = requested_app_id
        self.requested_group_tags = requested_group_tags if requested_group_tags is not None else []
        self.requested_ownership_ending_at = requested_ownership_ending_at
        self.request_reason = request_reason

        self.conditional_access_hook = get_conditional_access_hook()
        self.notification_hook = get_notification_hook()

    def execute(self) -> Optional[GroupRequest]:
        # Don't allow creating groups with -Owners suffix (reserved for app owner groups)
        if self.requested_group_name.endswith(f"-{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"):
            return None

        # Validate that app_id is only provided for AppGroup type
        if self.requested_app_id is not None and self.requested_group_type != AppGroup.__mapper_args__["polymorphic_identity"]:
            return None

        # Validate that app_id is provided if type is AppGroup
        if self.requested_group_type == AppGroup.__mapper_args__["polymorphic_identity"] and self.requested_app_id is None:
            return None

        # Validate app exists if app_id provided
        if self.requested_app_id is not None:
            app = db.session.query(App).filter(App.id == self.requested_app_id).filter(App.deleted_at.is_(None)).first()
            if app is None:
                return None

        # Validate tags exist and load them
        tags = []
        if self.requested_group_tags:
            tags = db.session.query(Tag).filter(Tag.id.in_(self.requested_group_tags)).filter(Tag.deleted_at.is_(None)).all()
            if len(tags) != len(self.requested_group_tags):
                return None

        # Apply tag ownership time constraints to requested_ownership_ending_at
        # Ensures that if tags have ownership time limits, they are enforced
        coalesced_ownership_ending_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=self.requested_ownership_ending_at,
            group_is_managed=True,  # Assume managed since group created through Access and not via Okta group rule
        )

        group_request = GroupRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=self.requester.id,
            requested_group_name=self.requested_group_name,
            requested_group_description=self.requested_group_description,
            requested_group_type=self.requested_group_type,
            requested_app_id=self.requested_app_id,
            requested_group_tags=self.requested_group_tags,
            requested_ownership_ending_at=coalesced_ownership_ending_at,
            request_reason=self.request_reason,
            # Initialize resolved fields with requested values TODO maybe not?
            # resolved_group_name=self.requested_group_name,
            # resolved_group_description=self.requested_group_description,
            # resolved_group_type=self.requested_group_type,
            # resolved_app_id=self.requested_app_id,
            # resolved_group_tags=self.requested_group_tags,
            # resolved_ownership_ending_at=coalesced_ownership_ending_at,
        )

        db.session.add(group_request)
        db.session.commit()

        # Fetch the users to notify
        # If app group, notify app managers; otherwise notify access owners
        if self.requested_app_id is not None:
            approvers = get_app_managers(self.requested_app_id)
        else:
            # TODO maybe change this to just platsec for RoleGroups, want to be able to enforce RBAC?
            approvers = get_access_owners()

        # Filter out the requester from approvers if they're the only one
        if len(approvers) == 1 and approvers[0].id == self.requester.id:
            if self.requested_app_id is not None:
                # Fall back to access owners if requester is the only app manager
                approvers = get_access_owners()
            # If still only the requester, keep them as the approver

        # Audit logging
        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema(exclude=["group_request.resolution_reason", "group_request.resolved_group_name", "group_request.resolved_group_description", "group_request.resolved_group_type", "group_request.resolved_app_id", "group_request.resolved_group_tags", "group_request.resolved_ownership_ending_at"]).dumps(
                {
                    "event_type": EventType.group_request_create,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.requester.id,
                    "current_user_email": self.requester.email,
                    "group_request": group_request,
                    "requester": self.requester,
                }
            )
        )

        # Check conditional access hook
        conditional_access_responses = self.conditional_access_hook.group_request_created(
            group_request=group_request,
            requester=self.requester,
        )

        for response in conditional_access_responses:
            if response is not None:
                if response.approved:
                    ApproveGroupRequest(
                        group_request=group_request,
                        approval_reason=response.reason,
                        notify=False,
                    ).execute()
                else:
                    RejectGroupRequest(
                        group_request=group_request,
                        rejection_reason=response.reason,
                        notify=False,
                    ).execute()

                return group_request

        # Send notification to approvers
        self.notification_hook.access_group_request_created(
            group_request=group_request,
            requester=self.requester,
            approvers=approvers,
        )

        return group_request

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))