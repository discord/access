import random
import string
import logging
from datetime import datetime
from typing import Optional

from fastapi import Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectin_polymorphic, selectinload

from api_v2.models import (
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
from api_v2.models.app_group import get_access_owners, get_app_managers
from api_v2.models.okta_group import get_group_managers
from api_v2.models.tag import coalesce_constraints
from api_v2.operations.approve_role_request import ApproveRoleRequest
from api_v2.operations.reject_role_request import RejectRoleRequest
from api_v2.plugins import get_conditional_access_hook, get_notification_hook
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary, AuditAppSummary, AuditRoleRequestSummary, AuditRoleGroupSummary, AuditUserSummary

logger = logging.getLogger(__name__)


class CreateRoleRequest:
    def __init__(
        self,
        db: Session,
        *,
        requester_user: OktaUser | str,
        requester_role: OktaGroup | str,
        requested_group: OktaGroup | str,
        request_ownership: bool = False,
        request_reason: str = "",
        request_ending_at: Optional[datetime] = None,
        request: Optional[Request] = None,
    ):
        self.db = db
        self.request = request
        self.id = self.__generate_id()

        if isinstance(requester_user, str):
            self.requester = self.db.get(OktaUser, requester_user)
        else:
            self.requester = requester_user

        if isinstance(requester_role, str):
            self.requester_role = (
                self.db.query(RoleGroup).filter(RoleGroup.deleted_at.is_(None)).filter(RoleGroup.id == requester_role).first()
            )
        else:
            self.requester_role = requester_role

        self.requested_group = (
            self.db.query(OktaGroup)
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

    def _log_audit_event(self, role_request: RoleRequest, group: OktaGroup, approvers: list[OktaUser]) -> None:
        """Log audit event for role request creation."""
        # Build audit data
        audit_data = {
            "event_type": AuditEventType.ROLE_REQUEST_CREATE,
            "user_agent": None,
            "ip": None,
            "current_user_id": self.requester.id,
            "current_user_email": self.requester.email,
            "group": AuditGroupSummary(
                id=group.id,
                name=group.name,
                type=group.type,
                app=AuditAppSummary(
                    id=group.app.id,
                    name=group.app.name
                ) if hasattr(group, 'app') and group.app else None
            ),
            "role_request": AuditRoleRequestSummary(
                id=role_request.id,
                requester_role=AuditRoleGroupSummary(
                    id=self.requester_role.id,
                    name=self.requester_role.name
                ),
                request_reason=role_request.request_reason,
                request_ending_at=role_request.request_ending_at,
                request_ownership=role_request.request_ownership
            ),
            "requester": AuditUserSummary(
                id=self.requester.id,
                email=self.requester.email,
                first_name=self.requester.first_name,
                last_name=self.requester.last_name,
                display_name=self.requester.display_name
            ),
            "group_owners": [
                AuditUserSummary(
                    id=approver.id,
                    email=approver.email,
                    first_name=approver.first_name,
                    last_name=approver.last_name,
                    display_name=approver.display_name
                ) for approver in approvers
            ],
        }

        if self.request:
            audit_data["user_agent"] = self.request.headers.get("User-Agent")
            audit_data["ip"] = (
                self.request.headers.get("X-Forwarded-For") or
                self.request.headers.get("X-Real-IP") or
                self.request.client.host if self.request.client else None
            )

        audit_log = AuditLogRead(**audit_data)
        logger.info(audit_log.model_dump_json(exclude_none=True))

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

        self.db.add(role_request)
        self.db.commit()

        # Fetch the users to notify
        approvers = get_group_managers(self.db, self.requested_group.id)

        requested_group_tags = [tm.active_tag for tm in self.requested_group.active_group_tags]

        role_memberships = [
            u.user_id
            for u in (
                self.db.query(OktaUserGroupMember).filter(OktaUserGroupMember.group_id == self.requester_role.id)
                .filter(OktaUserGroupMember.is_owner.is_(False))
                .filter(
                    or_(
                        OktaUserGroupMember.ended_at.is_(None),
                        OktaUserGroupMember.ended_at > func.now(),
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
        self._log_audit_event(role_request, group, approvers)

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
                        self.db,
                        role_request=role_request,
                        approval_reason=response.reason,
                        ending_at=response.ending_at,
                        notify=False,
                        request=self.request,
                    ).execute()
                else:
                    RejectRoleRequest(
                        self.db,
                        role_request=role_request,
                        rejection_reason=response.reason,
                        notify=False,
                        request=self.request,
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