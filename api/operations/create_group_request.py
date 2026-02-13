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
        # TODO for debugging remove
        print(f"DEBUG: Creating group request: {self.requested_group_name}, type: {self.requested_group_type}")

        # TODO for debugging remove
        print(f"DEBUG: Validation failed at line 65")
        # Don't allow creating groups with -Owners suffix (reserved for app owner groups)
        if self.requested_group_name.endswith(f"-{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"):
            return None

        # TODO for debugging remove
        print(f"DEBUG: Validation failed at line 71")
        # Validate that app_id is only provided for AppGroup type
        if self.requested_app_id is not None and self.requested_group_type != "app_group":
            return None
    
        # TODO for debugging remove
        print(f"DEBUG: Validation failed at line 77")
        # Validate that app_id is provided if type is AppGroup
        if self.requested_group_type == "app_group" and self.requested_app_id is None:
            return None
    
        # Validate app exists if app_id provided and desired group name prefix is correct
        app = None
        # TODO for debugging remove
        print(f"DEBUG: Validation failed at line 85")
        if self.requested_app_id is not None:
            app = db.session.query(App).options(
                selectinload(App.active_owner_app_groups).selectinload(AppGroup.active_user_memberships_and_ownerships)
            ).filter(App.id == self.requested_app_id).filter(App.deleted_at.is_(None)).first()
            
            if app is None:
                return None
            if not self.requested_group_name.startswith(f"App-{app.name}-"):
                return None
    
        # TODO for debugging remove
        print(f"DEBUG: Validation failed at line 94")
        # Validate tags exist and load them
        tags = []
        if self.requested_group_tags:
            tags = db.session.query(Tag).filter(Tag.id.in_(self.requested_group_tags)).filter(Tag.deleted_at.is_(None)).all()
            print(f"DEBUG: Requested {len(self.requested_group_tags)} tags, found {len(tags)} tags")
            print(f"DEBUG: Requested tag IDs: {self.requested_group_tags}")
            print(f"DEBUG: Found tag IDs: {[t.id for t in tags]}")
            if len(tags) != len(self.requested_group_tags):
                return None
    
        # Apply tag ownership time constraints to requested_ownership_ending_at
        # Ensures that if tags have ownership time limits, they are enforced
        coalesced_ownership_ending_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=self.requested_ownership_ending_at,
            group_is_managed=True,
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
        )
    
        db.session.add(group_request)
        db.session.commit()
    
        # If the requested group is an app group and the requester is the app owner, approve the request
        if self.requested_group_type == "app_group" and app is not None:
            # Get app owners by checking active owner app groups
            app_owner_user_ids = set()
            for owner_group in app.active_owner_app_groups:
                for membership in owner_group.active_user_memberships_and_ownerships:
                    app_owner_user_ids.add(membership.user_id)
            
            if self.requester.id in app_owner_user_ids:
                ApproveGroupRequest(
                    group_request=group_request,
                    approval_reason="Requester owns parent app and can create app groups",
                    notify=False,
                ).execute()
                return group_request
    
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
    
    def __generate_id(self) -> str:
        """Generate a random 20 character ID like Okta IDs"""
        return ''.join(random.choices(string.ascii_letters + string.digits, k=20))
