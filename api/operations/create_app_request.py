import logging
import random
import string
from datetime import datetime

from sqlalchemy import func, select

from api.context import get_request_context
from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import AccessRequestStatus, App, AppGroup, AppRequest, OktaUser, ServicePrincipal, Tag
from api.models.app_group import get_access_owners
from api.models.tag import coalesce_ended_at
from api.operations._active_ids import require_active_role_ids, require_active_user_ids
from api.operations._conditional_access import apply_conditional_access, current_status
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester, split_requester
from api.operations._service_principals import audit_ref
from api.operations.approve_app_request import ApproveAppRequest
from api.operations.create_app import owner_group_conflict
from api.operations.reject_app_request import RejectAppRequest
from api.plugins import ConditionalAccessHook, NotificationHook, evaluate_conditional_access
from api.schemas import AuditLogSchema, EventType
from api.schemas.app_requests import AppRequestGroup

logger = logging.getLogger(__name__)


async def validate_app_groups(app_name: str, app_groups: list[AppRequestGroup]) -> list[AppRequestGroup]:
    """Check an app request's initial app groups and return them with their ids deduplicated.

    Raises:
        InvalidRequestError: A name lacks the app's prefix, names its owners group,
            repeats, or a named owner or member is unknown.
    """
    prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    owners_group_name = f"{prefix}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    seen: set[str] = set()
    normalized = []
    for group in app_groups:
        if not group.name.startswith(prefix):
            raise InvalidRequestError(f"App group name must be prefixed with {prefix}")
        if group.name.lower() == owners_group_name.lower():
            raise InvalidRequestError(f"{owners_group_name} is created with the app; name its owners on the app")
        if group.name.lower() in seen:
            raise InvalidRequestError(f"App group {group.name} is named more than once")
        seen.add(group.name.lower())
        normalized.append(
            group.model_copy(
                update={
                    "owner_user_ids": await require_active_user_ids(group.owner_user_ids, "owner"),
                    "owner_role_ids": await require_active_role_ids(group.owner_role_ids, "owner"),
                    "member_user_ids": await require_active_user_ids(group.member_user_ids, "member"),
                    "member_role_ids": await require_active_role_ids(group.member_role_ids, "member"),
                }
            )
        )
    return normalized


def has_member_grants(app_groups: list[AppRequestGroup]) -> bool:
    """Whether any initial app group names members."""
    return any(group.member_user_ids or group.member_role_ids for group in app_groups)


async def ensure_app_name_available(app_name: str) -> None:
    """Raise `ConflictError` if an app named `app_name` can't be created."""
    existing = (
        await db.session.scalars(
            select(App.id).where(func.lower(App.name) == func.lower(app_name)).where(App.deleted_at.is_(None))
        )
    ).first()
    if existing is not None:
        raise ConflictError(f"An app named '{app_name}' already exists.")
    conflict = await owner_group_conflict(app_name)
    if conflict is not None:
        raise ConflictError(conflict)


class CreateAppRequest:
    """Create an app request, then auto-approve, reject, or route it to Access admins.

    The requester is a user or a service principal. A user's request that names
    no owners makes the user the app's owner; a service principal's request must
    name at least one owner, since a service principal is never made an owner.

    Raises:
        InvalidRequestError: A tag, owner, or member is unknown, an app group name is
            invalid, or a service principal's request names no owners.
        ConflictError: The app can't be created under the requested name.
    """

    def __init__(
        self,
        *,
        requester: OktaUser | ServicePrincipal | str,
        requested_app_name: str,
        requested_app_description: str = "",
        requested_app_tags: list[str] | None = None,
        requested_owner_user_ids: list[str] | None = None,
        requested_owner_role_ids: list[str] | None = None,
        requested_app_groups: list[AppRequestGroup] | None = None,
        requested_ownership_ending_at: datetime | None = None,
        requested_membership_ending_at: datetime | None = None,
        request_reason: str = "",
    ):
        self.id = "".join(random.choices(string.ascii_letters + string.digits, k=20))
        self.requester_id = requester if isinstance(requester, str) else requester.id
        self.requested_app_name = requested_app_name
        self.requested_app_description = requested_app_description
        self.requested_app_tags = requested_app_tags or []
        self.requested_owner_user_ids = requested_owner_user_ids or []
        self.requested_owner_role_ids = requested_owner_role_ids or []
        self.requested_app_groups = requested_app_groups or []
        self.requested_ownership_ending_at = requested_ownership_ending_at
        self.requested_membership_ending_at = requested_membership_ending_at
        self.request_reason = request_reason

    async def execute(self) -> AppRequest:
        """Create the request and return it."""
        requester = await load_active_requester(self.requester_id)
        assert requester is not None
        requester_user, requester_service_principal = split_requester(requester)

        await ensure_app_name_available(self.requested_app_name)

        tag_ids = list(dict.fromkeys(self.requested_app_tags))
        tags = list(
            (await db.session.scalars(select(Tag).where(Tag.id.in_(tag_ids)).where(Tag.deleted_at.is_(None)))).all()
        )
        if len(tags) != len(tag_ids):
            raise InvalidRequestError("One or more tags not found")

        owner_user_ids = await require_active_user_ids(self.requested_owner_user_ids, "owner")
        owner_role_ids = await require_active_role_ids(self.requested_owner_role_ids, "owner")
        if not owner_user_ids and not owner_role_ids:
            if requester_user is None:
                raise InvalidRequestError("A service principal's app request must name at least one owner")
            owner_user_ids = [requester_user.id]
        app_groups = await validate_app_groups(self.requested_app_name, self.requested_app_groups)

        app_request = AppRequest(
            id=self.id,
            status=AccessRequestStatus.PENDING,
            requester_user_id=requester_user.id if requester_user else None,
            requester_service_principal_id=requester_service_principal.id if requester_service_principal else None,
            requested_app_name=self.requested_app_name,
            requested_app_description=self.requested_app_description,
            requested_app_tags=tag_ids,
            requested_owner_user_ids=owner_user_ids,
            requested_owner_role_ids=owner_role_ids,
            requested_app_groups=[group.model_dump() for group in app_groups],
            requested_ownership_ending_at=coalesce_ended_at(
                Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, tags, self.requested_ownership_ending_at, group_is_managed=True
            ),
            # A request without members grants no membership, so it has no membership window.
            requested_membership_ending_at=(
                coalesce_ended_at(
                    Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY,
                    tags,
                    self.requested_membership_ending_at,
                    group_is_managed=True,
                )
                if has_member_grants(app_groups)
                else None
            ),
            request_reason=self.request_reason,
        )
        db.session.add(app_request)
        await db.session.commit()
        app_request_id = app_request.id

        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_request_create,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": requester.id,
                    "current_user_email": requester_user.email if requester_user else None,
                    "app_request": app_request,
                    "requester": requester_user,
                    "requester_service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        if await apply_conditional_access(
            await evaluate_conditional_access(
                ConditionalAccessHook.APP_REQUEST_CREATED, app_request=app_request, requester=requester
            ),
            request_label=f"app request {app_request.id}",
            approve=lambda r: ApproveAppRequest(
                app_request=app_request,
                approval_reason=r.reason,
                ending_at=r.ending_at,
                membership_ending_at=r.membership_ending_at,
                notify=False,
            ).execute(),
            reject=lambda r: RejectAppRequest(
                app_request=app_request, rejection_reason=r.reason, notify=False
            ).execute(),
            status=lambda: current_status(AppRequest, app_request_id),
        ):
            return app_request

        approvers = await get_access_owners()
        await defer_notification(
            db.session,
            NotificationHook.ACCESS_APP_REQUEST_CREATED,
            detach=[app_request, requester, *approvers],
            app_request=app_request,
            requester=requester,
            approvers=approvers,
        )
        return app_request
