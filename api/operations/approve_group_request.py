from datetime import datetime
from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import AccessRequestStatus, AppGroup, OktaGroup, OktaGroupTagMap, OktaUser, RoleGroup, GroupRequest, Tag
from api.models.tag import coalesce_ended_at
from api.operations.create_group import CreateGroup
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_group_tags import ModifyGroupTags
from api.plugins import get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class ApproveGroupRequest:
    def __init__(
        self,
        *,
        group_request: GroupRequest | str,
        approver_user: Optional[OktaUser | str] = None,
        approval_reason: str = "",
        notify: bool = True,
    ):
        self.group_request = (
            GroupRequest.query.options(
                joinedload(GroupRequest.active_requester)
            )
            .filter(GroupRequest.id == (group_request if isinstance(group_request, str) else group_request.id))
            .first()
        )

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

        self.notify = notify

        self.notification_hook = get_notification_hook()

    def execute(self) -> GroupRequest:
        # Don't allow approving a request that is already resolved
        if self.group_request.status != AccessRequestStatus.PENDING or self.group_request.resolved_at is not None:
            return self.group_request

        # Don't allow requester to approve their own request
        if self.group_request.requester_user_id == self.approver_id:
            return self.group_request

        # Don't allow approving a request if the requester is deleted
        requester = db.session.get(OktaUser, self.group_request.requester_user_id)
        if requester is None or requester.deleted_at is not None:
            return self.group_request

        db.session.commit()

        # Audit logging
        context = has_request_context()

        current_app.logger.info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_request_approve,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.approver_id,
                    "current_user_email": self.approver_email,
                    "group_request": self.group_request,
                    "requester": db.session.get(OktaUser, self.group_request.requester_user_id),
                }
            )
        )

        # Create the group based on resolved fields
        if self.group_request.resolved_group_type == "role_group":
            new_group = RoleGroup(
                name=self.group_request.resolved_group_name,
                description=self.group_request.resolved_group_description,
            )
        elif self.group_request.resolved_group_type == "app_group":
            new_group = AppGroup(
                name=self.group_request.resolved_group_name,
                description=self.group_request.resolved_group_description,
                app_id=self.group_request.resolved_app_id,
            )
        else:
            new_group = OktaGroup(
                name=self.group_request.resolved_group_name,
                description=self.group_request.resolved_group_description,
            )

        created_group = CreateGroup(
            group=new_group,
            tags=self.group_request.resolved_group_tags,
            current_user_id=self.approver_id,
        ).execute()

        # Check tags on created group for ownership length constraints including propagated app tags
        created_group_with_tags = (
            db.session.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
            )
            .filter(OktaGroup.id == created_group.id)
            .filter(OktaGroup.deleted_at.is_(None))
            .first()
        )

        tags = [tag_map.active_tag for tag_map in created_group_with_tags.active_group_tags]

        coalesced_ownership_ending_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=self.group_request.resolved_ownership_ending_at,
            group_is_managed=True,
        )

        # Update the group request with the coalesced value to ensure consistency
        self.group_request.resolved_ownership_ending_at = coalesced_ownership_ending_at

        # Add the requester as an owner of the newly created group
        ModifyGroupUsers(
            group=created_group_with_tags,
            owners_to_add=[self.group_request.requester_user_id],
            users_added_ended_at=coalesced_ownership_ending_at,
            current_user_id=self.approver_id,
            created_reason=f'Group request approved: {self.group_request.request_reason}',
            notify=self.notify,
        ).execute()

        self.group_request.status = AccessRequestStatus.APPROVED
        self.group_request.resolved_at = db.func.now()
        self.group_request.resolver_user_id = self.approver_id
        self.group_request.resolution_reason = self.approval_reason
        self.group_request.approved_group_id = created_group.id

        db.session.commit()

        return self.group_request
