import random
import string
from datetime import datetime
from typing import Optional

import logging

from sqlalchemy import select
from api.context import get_request_context
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload

from api.extensions import db
from api.models import (
    AccessRequestStatus,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    RoleGroup,
    RoleRequest,
)
from api.models.request_reviewers import get_assigned_reviewers
from api.operations.approve_role_request import ApproveRoleRequest
from api.operations.reject_role_request import RejectRoleRequest
from api.operations._fan_out import defer_notification
from api.plugins import ConditionalAccessHook, NotificationHook, evaluate_conditional_access
from api.schemas import AuditLogSchema, EventType


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

        self.requester_user_id = requester_user if isinstance(requester_user, str) else requester_user.id
        self.requester_role_id = requester_role if isinstance(requester_role, str) else requester_role.id
        self.requested_group_id = requested_group if isinstance(requested_group, str) else requested_group.id

        self.request_ownership = request_ownership
        self.request_reason = request_reason
        self.request_ending_at = request_ending_at

    async def execute(self) -> Optional[RoleRequest]:
        requester = await db.session.get(OktaUser, self.requester_user_id)
        assert requester is not None

        requester_role = (
            await db.session.scalars(
                select(RoleGroup).where(RoleGroup.deleted_at.is_(None)).where(RoleGroup.id == self.requester_role_id)
            )
        ).first()
        assert requester_role is not None

        requested_group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    selectin_polymorphic(OktaGroup, [AppGroup]),
                    joinedload(AppGroup.app),
                    selectinload(OktaGroup.active_group_tags).options(joinedload(OktaGroupTagMap.active_tag)),
                )
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == self.requested_group_id)
            )
        ).first()
        assert requested_group is not None

        # Don't allow creating a request for an unmanaged group
        if not requested_group.is_managed:
            return None

        # Don't allow creating a request for a role group
        if type(requested_group) is RoleGroup:
            return None

        role_request = RoleRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=requester.id,
            requester_role_id=requester_role.id,
            requested_group_id=requested_group.id,
            request_ownership=self.request_ownership,
            request_reason=self.request_reason,
            request_ending_at=self.request_ending_at,
        )

        db.session.add(role_request)
        await db.session.commit()

        approvers = await get_assigned_reviewers(role_request)

        group = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    joinedload(AppGroup.app),
                    selectinload(OktaGroup.active_group_tags).options(
                        joinedload(OktaGroupTagMap.active_app_tag_mapping),
                        joinedload(OktaGroupTagMap.enabled_active_tag),
                    ),
                )
                .where(OktaGroup.deleted_at.is_(None))
                .where(OktaGroup.id == requested_group.id)
            )
        ).first()
        assert group is not None

        # Audit logging
        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema(exclude=["request.resolution_reason", "request.approval_ending_at"]).dumps(
                {
                    "event_type": EventType.role_request_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": requester.id,
                    "current_user_email": requester.email,
                    "group": group,
                    "role_request": role_request,
                    "requester": requester,
                    "group_owners": approvers,
                }
            )
        )

        conditional_access_responses = await evaluate_conditional_access(
            ConditionalAccessHook.ROLE_REQUEST_CREATED,
            role_request=role_request,
            role=requester_role,
            group=requested_group,
            group_tags=[active_tag_map.enabled_active_tag for active_tag_map in group.active_group_tags],
            requester=requester,
            requester_role=requester_role,
        )

        for response in conditional_access_responses:
            if response is not None:
                if response.approved:
                    await ApproveRoleRequest(
                        role_request=role_request,
                        approval_reason=response.reason,
                        ending_at=response.ending_at,
                        notify=False,
                    ).execute()
                else:
                    await RejectRoleRequest(
                        role_request=role_request,
                        rejection_reason=response.reason,
                        notify=False,
                    ).execute()

                return role_request

        await defer_notification(
            db.session,
            NotificationHook.ACCESS_ROLE_REQUEST_CREATED,
            detach=[role_request, requester_role, requested_group, requester, *approvers],
            role_request=role_request,
            role=requester_role,
            group=requested_group,
            requester=requester,
            approvers=approvers,
        )

        return role_request

    # Generate a 20 character alphanumeric ID similar to Okta IDs for users and groups
    def __generate_id(self) -> str:
        return "".join(random.choices(string.ascii_letters, k=20))
