import logging
import random
import string
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from api.context import get_request_context
from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import AccessRequestStatus, App, AppGroup, AppRequest, OktaUser, ServicePrincipal, Tag
from api.models.app_group import get_access_owners
from api.models.tag import coalesce_ended_at
from api.operations._active_ids import require_active_role_ids, require_active_user_ids
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester
from api.operations._service_principals import audit_ref
from api.operations.approve_app_request import ApproveAppRequest
from api.operations.create_app import owner_group_conflict
from api.operations.reject_app_request import RejectAppRequest
from api.plugins import ConditionalAccessHook, NotificationHook, evaluate_conditional_access
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


async def validate_app_groups(app_name: str, app_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check an app request's initial app groups and return them normalized.

    Raises:
        InvalidRequestError: A name lacks the app's prefix, names its owners group,
            repeats, or a named owner or member is unknown.
    """
    prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
    owners_group_name = f"{prefix}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
    seen: set[str] = set()
    normalized = []
    for group in app_groups:
        name = group["name"]
        if not name.startswith(prefix):
            raise InvalidRequestError(f"App group name must be prefixed with {prefix}")
        if name.lower() == owners_group_name.lower():
            raise InvalidRequestError(f"{owners_group_name} is created with the app; name its owners on the app")
        if name.lower() in seen:
            raise InvalidRequestError(f"App group {name} is named more than once")
        seen.add(name.lower())
        normalized.append(
            {
                "name": name,
                "description": group.get("description") or "",
                "owner_user_ids": await require_active_user_ids(group.get("owner_user_ids", []), "owner"),
                "owner_role_ids": await require_active_role_ids(group.get("owner_role_ids", []), "owner"),
                "member_user_ids": await require_active_user_ids(group.get("member_user_ids", []), "member"),
                "member_role_ids": await require_active_role_ids(group.get("member_role_ids", []), "member"),
            }
        )
    return normalized


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
        requested_app_groups: list[dict[str, Any]] | None = None,
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
        requester_user = requester if isinstance(requester, OktaUser) else None
        requester_service_principal = requester if isinstance(requester, ServicePrincipal) else None

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
            requested_app_groups=app_groups,
            requested_ownership_ending_at=coalesce_ended_at(
                Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, tags, self.requested_ownership_ending_at, group_is_managed=True
            ),
            requested_membership_ending_at=coalesce_ended_at(
                Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, tags, self.requested_membership_ending_at, group_is_managed=True
            ),
            request_reason=self.request_reason,
        )
        db.session.add(app_request)
        await db.session.commit()

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
                    "service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        for response in await evaluate_conditional_access(
            ConditionalAccessHook.APP_REQUEST_CREATED, app_request=app_request, requester=requester
        ):
            if response is None:
                continue
            if response.approved:
                try:
                    await ApproveAppRequest(
                        app_request=app_request,
                        approval_reason=response.reason,
                        ending_at=response.ending_at,
                        membership_ending_at=response.membership_ending_at,
                        notify=False,
                    ).execute()
                except (ConflictError, InvalidRequestError) as e:
                    logger.warning("Conditional access approval of app request %s failed: %s", app_request.id, e)
                # An approval that couldn't create the app as requested leaves the
                # request pending; route it to Access admins.
                if app_request.status == AccessRequestStatus.PENDING:
                    break
            else:
                await RejectAppRequest(
                    app_request=app_request, rejection_reason=response.reason, notify=False
                ).execute()
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
