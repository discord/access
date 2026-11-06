import logging
import random
import string
from datetime import datetime

from sqlalchemy import select

from api.context import get_request_context
from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    GroupRequest,
    OktaUser,
    RoleGroup,
    ServicePrincipal,
    Tag,
)
from api.models.app_group import get_access_owners, get_app_managers
from api.models.okta_group import is_group_name_taken
from api.models.tag import coalesce_ended_at
from api.operations._active_ids import require_active_role_ids, require_active_user_ids
from api.operations._conditional_access import apply_conditional_access, current_status
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester, split_requester
from api.operations._service_principals import audit_ref
from api.operations.approve_group_request import ApproveGroupRequest
from api.operations.reject_group_request import RejectGroupRequest
from api.plugins import (
    ConditionalAccessHook,
    ConditionalAccessResponse,
    NotificationHook,
    evaluate_conditional_access,
)
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


class CreateGroupRequest:
    """Create a group request, then auto-approve, reject, or route it to its approvers.

    The requester is a user or a service principal. A user's request that names
    no owners makes the user the owner; a service principal's request must name
    at least one owner, since a service principal is never made an owner.

    Raises:
        InvalidRequestError: A named owner or member is unknown, roles are named on a
            role request, or a service principal's request names no owners.
        ConflictError: A group with the requested name already exists.
    """

    def __init__(
        self,
        *,
        requester_user: OktaUser | ServicePrincipal | str,
        requested_group_name: str,
        requested_group_description: str = "",
        requested_group_type: str,
        requested_app_id: str | None = None,
        requested_group_tags: list[str] | None = None,
        requested_ownership_ending_at: datetime | None = None,
        request_reason: str = "",
        requested_plugin_data: dict | None = None,
        requested_owner_user_ids: list[str] | None = None,
        requested_owner_role_ids: list[str] | None = None,
        requested_member_user_ids: list[str] | None = None,
        requested_member_role_ids: list[str] | None = None,
        requested_membership_ending_at: datetime | None = None,
    ):
        self.id = self.__generate_id()

        self.requester_id = requester_user if isinstance(requester_user, str) else requester_user.id

        self.requested_group_name = requested_group_name
        self.requested_group_description = requested_group_description
        self.requested_group_type = requested_group_type
        self.requested_app_id = requested_app_id
        self.requested_group_tags = requested_group_tags if requested_group_tags is not None else []
        self.requested_ownership_ending_at = requested_ownership_ending_at
        self.request_reason = request_reason
        self.requested_plugin_data = requested_plugin_data if requested_plugin_data is not None else {}
        self.requested_owner_user_ids = requested_owner_user_ids or []
        self.requested_owner_role_ids = requested_owner_role_ids or []
        self.requested_member_user_ids = requested_member_user_ids or []
        self.requested_member_role_ids = requested_member_role_ids or []
        self.requested_membership_ending_at = requested_membership_ending_at

    async def execute(self) -> GroupRequest | None:
        requester = await load_active_requester(self.requester_id)
        assert requester is not None
        requester_user, requester_service_principal = split_requester(requester)

        # Don't allow creating groups with -Owners suffix (reserved for app owner groups)
        if self.requested_group_name.endswith(f"-{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"):
            return None

        # Validate that app_id is only provided for AppGroup type
        if self.requested_app_id is not None and self.requested_group_type != "app_group":
            return None

        # Validate that role prefix is used for all role groups and prefix is not used for other group types
        if self.requested_group_type == "role_group" and not self.requested_group_name.startswith(
            RoleGroup.ROLE_GROUP_NAME_PREFIX
        ):
            return None

        if self.requested_group_type != "role_group" and self.requested_group_name.startswith(
            RoleGroup.ROLE_GROUP_NAME_PREFIX
        ):
            return None

        # Validate that app_id is provided if type is AppGroup
        if self.requested_group_type == "app_group" and self.requested_app_id is None:
            return None

        # Validate app exists if app_id provided and desired group name prefix is correct
        app = None
        if self.requested_app_id is not None:
            app = (
                await db.session.scalars(
                    select(App).where(
                        App.id == self.requested_app_id,
                        App.deleted_at.is_(None),
                    )
                )
            ).first()

            if app is None:
                return None
            if not self.requested_group_name.startswith(f"App-{app.name}-"):
                return None

        # Validate tags exist and load them
        tags = []
        if self.requested_group_tags:
            tags = list(
                (
                    await db.session.scalars(
                        select(Tag).where(Tag.id.in_(self.requested_group_tags)).where(Tag.deleted_at.is_(None))
                    )
                ).all()
            )
            if len(tags) != len(self.requested_group_tags):
                return None

        # Approval can never create a group whose name is taken, so refuse the
        # request now instead of leaving it for a reviewer to discover.
        if await is_group_name_taken(self.requested_group_name):
            raise ConflictError(f"A group named '{self.requested_group_name}' already exists.")

        owner_user_ids = await require_active_user_ids(self.requested_owner_user_ids, "owner")
        owner_role_ids = await require_active_role_ids(self.requested_owner_role_ids, "owner")
        member_user_ids = await require_active_user_ids(self.requested_member_user_ids, "member")
        member_role_ids = await require_active_role_ids(self.requested_member_role_ids, "member")
        if self.requested_group_type == "role_group" and (owner_role_ids or member_role_ids):
            raise InvalidRequestError("Roles cannot be added to other Roles")
        # A request that names no owners makes its requester the owner. A service
        # principal is never an owner, so its request must name one.
        if not owner_user_ids and not owner_role_ids:
            if requester_user is None:
                raise InvalidRequestError("A service principal's group request must name at least one owner")
            owner_user_ids = [requester_user.id]

        # Apply tag ownership time constraints to requested_ownership_ending_at
        # Ensures that if tags have ownership time limits, they are enforced
        coalesced_ownership_ending_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=self.requested_ownership_ending_at,
            group_is_managed=True,
        )
        # A request without members grants no membership, so it has no membership window.
        coalesced_membership_ending_at = (
            coalesce_ended_at(
                constraint_key=Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY,
                tags=tags,
                initial_ended_at=self.requested_membership_ending_at,
                group_is_managed=True,
            )
            if member_user_ids or member_role_ids
            else None
        )

        group_request = GroupRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=requester_user.id if requester_user else None,
            requester_service_principal_id=requester_service_principal.id if requester_service_principal else None,
            requested_group_name=self.requested_group_name,
            requested_group_description=self.requested_group_description,
            requested_group_type=self.requested_group_type,
            requested_app=app,
            requested_group_tags=self.requested_group_tags,
            requested_ownership_ending_at=coalesced_ownership_ending_at,
            requested_owner_user_ids=owner_user_ids,
            requested_owner_role_ids=owner_role_ids,
            requested_member_user_ids=member_user_ids,
            requested_member_role_ids=member_role_ids,
            requested_membership_ending_at=coalesced_membership_ending_at,
            request_reason=self.request_reason,
            requested_plugin_data=self.requested_plugin_data,
        )

        db.session.add(group_request)
        await db.session.commit()
        group_request_id = group_request.id

        # If the requested group is an app group and the requester is the app owner, approve the request
        if self.requested_group_type == "app_group" and app is not None and requester_user is not None:
            # Get app owners by checking active owner app groups
            app_owner_user_ids = {u.id for u in await get_app_managers(app.id)}

            if requester_user.id in app_owner_user_ids:
                try:
                    await ApproveGroupRequest(
                        group_request=group_request,
                        approver_user=requester_user,
                        approval_reason="Requester owns parent app and can create app groups",
                        notify=False,
                        bypass_self_approval=True,
                    ).execute()
                    return group_request
                except ConflictError as e:
                    # The name was taken after the check above; the request is
                    # already saved, so route it to reviewers like any other.
                    logger.warning("App owner auto-approval of group request %s failed: %s", group_request.id, e)

        # Fetch the users to notify
        # If app group, notify app managers; otherwise notify access owners
        if self.requested_app_id is not None:
            approvers = await get_app_managers(self.requested_app_id)
        else:
            approvers = await get_access_owners()

        # Audit logging
        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema(
                exclude=[
                    "group_request.resolution_reason",
                    "group_request.resolved_group_name",
                    "group_request.resolved_group_description",
                    "group_request.resolved_group_type",
                    "group_request.resolved_app_id",
                    "group_request.resolved_group_tags",
                    "group_request.resolved_ownership_ending_at",
                    "group_request.resolved_owner_user_ids",
                    "group_request.resolved_owner_role_ids",
                    "group_request.resolved_member_user_ids",
                    "group_request.resolved_member_role_ids",
                    "group_request.resolved_membership_ending_at",
                ]
            ).dumps(
                {
                    "event_type": EventType.group_request_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": requester.id,
                    "current_user_email": requester_user.email if requester_user else None,
                    "group_request": group_request,
                    "requester": requester_user,
                    "requester_service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        # Check conditional access hook
        conditional_access_responses = await evaluate_conditional_access(
            ConditionalAccessHook.GROUP_REQUEST_CREATED,
            group_request=group_request,
            requester=requester,
        )

        # A plugin approval grants whatever the request names. Rules written before
        # requests could name grants approved only "the requester owns the new
        # group", so an approval of a request naming anything more must opt in.
        names_more_than_requester = not (
            owner_user_ids == [requester.id] and not owner_role_ids and not member_user_ids and not member_role_ids
        )

        def honored(response: ConditionalAccessResponse | None) -> ConditionalAccessResponse | None:
            if (
                response is not None
                and response.approved
                and names_more_than_requester
                and not response.allow_named_grants
            ):
                logger.warning(
                    "Conditional access approval of group request %s ignored: it names grants beyond its "
                    "requester and the response did not set allow_named_grants",
                    group_request.id,
                )
                return None
            return response

        if await apply_conditional_access(
            [honored(r) for r in conditional_access_responses],
            request_label=f"group request {group_request.id}",
            approve=lambda r: ApproveGroupRequest(
                group_request=group_request,
                approval_reason=r.reason,
                ending_at=r.ending_at,
                membership_ending_at=r.membership_ending_at,
                notify=False,
            ).execute(),
            reject=lambda r: RejectGroupRequest(
                group_request=group_request, rejection_reason=r.reason, notify=False
            ).execute(),
            status=lambda: current_status(GroupRequest, group_request_id),
        ):
            return group_request

        # Send notification to approvers (deferred to after the response)
        await defer_notification(
            db.session,
            NotificationHook.ACCESS_GROUP_REQUEST_CREATED,
            # `app` too: hooks may read it via group_request.requested_app
            detach=[group_request, app, requester, *approvers],
            group_request=group_request,
            requester=requester,
            approvers=approvers,
        )

        return group_request

    def __generate_id(self) -> str:
        """Generate a random 20 character ID like Okta IDs"""
        return "".join(random.choices(string.ascii_letters + string.digits, k=20))
