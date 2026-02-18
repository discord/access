from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import (
    AccessRequestStatus,
    AppGroup,
    GroupRequest,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    Tag,
)
from api.models.app_group import get_access_owners
from api.models.tag import coalesce_ended_at
from api.operations.constraints.check_for_self_add import CheckForSelfAdd
from api.operations.create_group import CreateGroup
from api.operations.modify_group_users import ModifyGroupUsers
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
        bypass_self_approval: bool = False,
    ):
        self.group_request = (
            GroupRequest.query.options(joinedload(GroupRequest.active_requester))
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
        self.bypass_self_approval = bypass_self_approval
        self.notification_hook = get_notification_hook()

    def execute(self) -> Optional[GroupRequest]:
        # Guard against missing group_request
        if self.group_request is None:
            return None

        # Don't allow approving a request that is already resolved
        if self.group_request.status != AccessRequestStatus.PENDING or self.group_request.resolved_at is not None:
            return self.group_request

        # Don't allow requester to approve their own request
        if self.group_request.requester_user_id == self.approver_id and not self.bypass_self_approval:
            return self.group_request

        # Don't allow approving a request if the requester is deleted
        requester = db.session.get(OktaUser, self.group_request.requester_user_id)
        if requester is None or requester.deleted_at is not None:
            return self.group_request

        # Resolve group fields: use resolved_* if set, otherwise fall back to requested_*
        resolved_name = (
            self.group_request.resolved_group_name
            if self.group_request.resolved_group_name
            else self.group_request.requested_group_name
        )
        resolved_description = (
            self.group_request.resolved_group_description
            if self.group_request.resolved_group_description
            else self.group_request.requested_group_description
        )
        resolved_type = (
            self.group_request.resolved_group_type
            if self.group_request.resolved_group_type
            else self.group_request.requested_group_type
        )
        resolved_app_id = (
            self.group_request.resolved_app_id
            if self.group_request.resolved_app_id
            else self.group_request.requested_app_id
        )
        resolved_tags = (
            self.group_request.resolved_group_tags
            if self.group_request.resolved_group_tags
            else self.group_request.requested_group_tags
        )

        # authorization
        access_owner_ids = {u.id for u in get_access_owners()}
        is_admin = self.approver_id in access_owner_ids

        if resolved_app_id is not None:
            # App group request: admins OR owners of that specific app can approve
            if not is_admin:
                is_app_owner = (
                    db.session.query(OktaUserGroupMember)
                    .join(AppGroup, OktaUserGroupMember.group_id == AppGroup.id)
                    .filter(
                        AppGroup.app_id == resolved_app_id,
                        AppGroup.is_owner.is_(True),
                        AppGroup.deleted_at.is_(None),
                        OktaUserGroupMember.user_id == self.approver_id,
                        OktaUserGroupMember.ended_at.is_(None),
                    )
                    .first()
                )

                if not is_app_owner:
                    return self.group_request
        else:
            # okta_group / role_group request: only admins can approve
            if not is_admin:
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
        if resolved_type == "role_group":
            new_group = RoleGroup(
                name=resolved_name,
                description=resolved_description,
            )
        elif resolved_type == "app_group":
            new_group = AppGroup(
                name=resolved_name,
                description=resolved_description,
                app_id=resolved_app_id,
            )
        else:
            new_group = OktaGroup(
                name=resolved_name,
                description=resolved_description,
            )

        created_group = CreateGroup(
            group=new_group,
            tags=resolved_tags,
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

        # Determine the initial ending time: prefer resolved, fall back to requested
        initial_ending_at = (
            self.group_request.resolved_ownership_ending_at
            if self.group_request.resolved_ownership_ending_at
            else self.group_request.requested_ownership_ending_at
        )

        coalesced_ownership_ending_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=initial_ending_at,
            group_is_managed=True,
        )

        # Update the group request with the coalesced value to ensure consistency
        self.group_request.resolved_ownership_ending_at = coalesced_ownership_ending_at

        # Add the requester as an owner of the newly created group
        # If app owner auto approval, skip if group has owner add constraint (will inherit ownership via app)
        can_add_owner = True
        if self.bypass_self_approval:
            can_add_owner, _ = CheckForSelfAdd(
                group=created_group_with_tags, current_user=self.approver_id, owners_to_add=[self.approver_id]
            ).execute_for_group()

        if can_add_owner:
            ModifyGroupUsers(
                group=created_group_with_tags,
                owners_to_add=[self.group_request.requester_user_id],
                users_added_ended_at=coalesced_ownership_ending_at,
                current_user_id=self.approver_id,
                created_reason=f"Group request approved: {self.group_request.request_reason}",
                notify=self.notify,
            ).execute()

        self.group_request.status = AccessRequestStatus.APPROVED
        self.group_request.resolved_at = db.func.now()
        self.group_request.resolver_user_id = self.approver_id
        self.group_request.resolution_reason = self.approval_reason
        self.group_request.approved_group_id = created_group.id

        db.session.commit()

        return self.group_request
