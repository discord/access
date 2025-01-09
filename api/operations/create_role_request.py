import random
import string
from datetime import datetime
from typing import Optional

from flask import current_app, has_request_context, request
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import (
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleRequest,
    Tag,
)
from api.models.app_group import get_access_owners, get_app_managers
from api.models.okta_group import get_group_managers
from api.models.tag import coalesce_constraints
from api.operations.approve_role_request import ApproveRoleRequest
from api.operations.reject_role_request import RejectRoleRequest
from api.plugins import get_conditional_access_hook, get_notification_hook
from api.views.schemas import AuditLogSchema, EventType


class CreateRoleRequest:
    def __init__(
        self,
        *,
        requester_user: OktaUser | str,
        requester_role: OktaGroup | str,
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

        if isinstance(requester_role, str):
            self.requester_role = (
                RoleGroup.query.filter(RoleGroup.deleted_at.is_(None)).filter(RoleGroup.id == requester_role).first()
            )
            # self.requester_role = (
            #     db.session.query(RoleGroup)
            #     .options(joinedload(OktaUserGroupMember.user))
            #     .filter(RoleGroup.deleted_at.is_(None))
            #     .filter(RoleGroup.id == requester_role)
            #     .first()
            # )
        else:
            self.requester_role = requester_role

        self.requested_group = (
            db.session.query(OktaGroup)
            .options(
                selectin_polymorphic(OktaGroup, [AppGroup]),
                joinedload(AppGroup.app),
                selectinload(OktaGroup.active_group_tags).options(joinedload(OktaGroupTagMap.active_tag)),
            )
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == (requested_group if isinstance(requested_group, str) else requested_group.id))
            .first()
        )

        self.request_ownership = request_ownership
        self.request_reason = request_reason
        self.request_ending_at = request_ending_at

        self.conditional_access_hook = get_conditional_access_hook()
        self.notification_hook = get_notification_hook()

    def execute(self) -> Optional[RoleRequest]:
        # Don't allow creating a request for an unmanaged group
        if not self.requested_group.is_managed:
            return None

        # Don't allow creating a request for a role group
        if type(self.requested_group) is RoleGroup:
            return None

        role_request = RoleRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=self.requester.id,
            requester_role_id=self.requester_role.id,
            requested_group_id=self.requested_group.id,
            request_ownership=self.request_ownership,
            request_reason=self.request_reason,
            request_ending_at=self.request_ending_at,
        )

        db.session.add(role_request)
        db.session.commit()

        # Fetch the users to notify
        approvers = get_group_managers(self.requested_group.id)

        requested_group_tags = [tm.active_tag for tm in self.requested_group.active_group_tags]

        role_memberships = [
            u.user_id
            for u in (
                OktaUserGroupMember.query.filter(OktaUserGroupMember.group_id == self.requester_role.id)
                .filter(OktaUserGroupMember.is_owner.is_(False))
                .filter(
                    db.or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > db.func.now(),
                    )
                )
                .all()
            )
        ]

        # If group tagged with disallow self add constraint, filter out approvers who are also members of the role
        if self.request_ownership:
            disallow_self_add_owner = coalesce_constraints(
                constraint_key=Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
                tags=requested_group_tags,
            )
            if disallow_self_add_owner:
                approvers = [a for a in approvers if a.id not in role_memberships]
        else:
            disallow_self_add_member = coalesce_constraints(
                constraint_key=Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
                tags=requested_group_tags,
            )
            if disallow_self_add_member:
                approvers = [a for a in approvers if a.id not in role_memberships]

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
                    "event_type": EventType.role_request_create,
                    "user_agent": request.headers.get("User-Agent") if context else None,
                    "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
                    if context
                    else None,
                    "current_user_id": self.requester.id,
                    "current_user_email": self.requester.email,
                    "group": group,
                    "role_request": role_request,
                    "requester": self.requester,
                    "group_owners": approvers,
                }
            )
        )

        conditional_access_responses = self.conditional_access_hook.role_request_created(
            role_request=role_request,
            role=self.requester_role,
            group=self.requested_group,
            group_tags=[active_tag_map.enabled_active_tag for active_tag_map in group.active_group_tags],
            requester=self.requester,
            requester_role=self.requester_role,
        )

        for response in conditional_access_responses:
            if response is not None:
                if response.approved:
                    ApproveRoleRequest(
                        role_request=role_request,
                        approval_reason=response.reason,
                        ending_at=response.ending_at,
                        notify=False,
                    ).execute()
                else:
                    RejectRoleRequest(
                        role_request=role_request,
                        rejection_reason=response.reason,
                        notify=False,
                    ).execute()

                return role_request

        self.notification_hook.access_role_request_created(
            role_request=role_request,
            role=self.requester_role,
            group=self.requested_group,
            requester=self.requester,
            approvers=approvers,
        )

        return role_request

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
