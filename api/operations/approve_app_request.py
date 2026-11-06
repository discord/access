import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from api.context import get_request_context
from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import AccessRequestStatus, App, AppGroup, AppRequest, OktaUser, ServicePrincipal
from api.models.app_group import get_access_owners
from api.operations._active_ids import require_active_role_ids, require_active_user_ids
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester
from api.operations._service_principals import audit_ref
from api.operations.create_app import CreateApp, owner_group_conflict
from api.operations.create_group import GroupDict
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_role_groups import ModifyRoleGroups
from api.plugins import NotificationHook
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _capped(requested: datetime | None, cap: datetime | None) -> datetime | None:
    """`requested`, shortened to `cap` when `cap` is earlier; a cap never extends it."""
    if requested is not None:
        requested = _as_utc(requested)
    if cap is None:
        return requested
    cap = _as_utc(cap)
    return cap if requested is None else min(requested, cap)


class ApproveAppRequest:
    """Approve a pending app request: create the app, its owners group, and its initial
    app groups, and grant the owners and members the request names.

    Only Access admins approve app requests; with no approver, a conditional-access
    plugin is approving it.

    Args:
        app_request: The request or its id.
        approver_user: The approving Access admin, or None for a plugin approval.
        approval_reason: Recorded as the resolution reason.
        ending_at: Caps the owner grants; it never extends them.
        membership_ending_at: Caps the member grants; it never extends them.
        notify: Whether to send notifications.

    Raises:
        ConflictError: The request is no longer pending, or the app can't be created
            under its name.
        InvalidRequestError: An end date has passed, or a named owner or member is no
            longer active.
    """

    def __init__(
        self,
        *,
        app_request: AppRequest | str,
        approver_user: OktaUser | str | None = None,
        approval_reason: str = "",
        ending_at: datetime | None = None,
        membership_ending_at: datetime | None = None,
        notify: bool = True,
    ):
        self.app_request_id = app_request if isinstance(app_request, str) else app_request.id
        self.approver_user_id = approver_user if not isinstance(approver_user, OktaUser) else approver_user.id
        self.approval_reason = approval_reason
        self.ending_at = ending_at
        self.membership_ending_at = membership_ending_at
        self.notify = notify

    async def execute(self) -> AppRequest | None:
        """Approve the request and return it, or return it unchanged when the approver may not approve it."""
        app_request = (
            await db.session.scalars(select(AppRequest).where(AppRequest.id == self.app_request_id).with_for_update())
        ).first()
        if app_request is None:
            return None
        if app_request.status != AccessRequestStatus.PENDING or app_request.resolved_at is not None:
            raise ConflictError("App request is no longer pending")

        approver: OktaUser | None = None
        if self.approver_user_id is not None:
            approver = (
                await db.session.scalars(
                    select(OktaUser).where(OktaUser.id == self.approver_user_id).where(OktaUser.deleted_at.is_(None))
                )
            ).first()
            if approver is None or approver.id not in {u.id for u in await get_access_owners()}:
                return app_request
            if approver.id == app_request.requester_user_id:
                return app_request

        requester = await load_active_requester(
            app_request.requester_service_principal_id or app_request.requester_user_id
        )
        if requester is None:
            return app_request
        requester_user = requester if isinstance(requester, OktaUser) else None
        requester_service_principal = requester if isinstance(requester, ServicePrincipal) else None

        ownership_ending_at = _capped(
            app_request.resolved_ownership_ending_at or app_request.requested_ownership_ending_at, self.ending_at
        )
        membership_ending_at = _capped(
            app_request.resolved_membership_ending_at or app_request.requested_membership_ending_at,
            self.membership_ending_at,
        )
        now = datetime.now(UTC)
        if ownership_ending_at is not None and ownership_ending_at <= now:
            raise InvalidRequestError(
                "The ownership window for this request has already passed. Set an ownership end date when approving it."
            )
        if membership_ending_at is not None and membership_ending_at <= now:
            raise InvalidRequestError(
                "The membership window for this request has already passed. "
                "Set a membership end date when approving it."
            )

        # The app may have been created, or its name claimed, since the request was filed.
        existing = (
            await db.session.scalars(
                select(App.id)
                .where(func.lower(App.name) == func.lower(app_request.requested_app_name))
                .where(App.deleted_at.is_(None))
            )
        ).first()
        if existing is not None:
            raise ConflictError(f"An app named '{app_request.requested_app_name}' already exists.")
        conflict = await owner_group_conflict(app_request.requested_app_name)
        if conflict is not None:
            raise ConflictError(conflict)

        owner_user_ids = await require_active_user_ids(app_request.requested_owner_user_ids, "owner")
        owner_role_ids = await require_active_role_ids(app_request.requested_owner_role_ids, "owner")
        app_groups: list[dict[str, Any]] = [
            {
                **group,
                "owner_user_ids": await require_active_user_ids(group.get("owner_user_ids", []), "owner"),
                "owner_role_ids": await require_active_role_ids(group.get("owner_role_ids", []), "owner"),
                "member_user_ids": await require_active_user_ids(group.get("member_user_ids", []), "member"),
                "member_role_ids": await require_active_role_ids(group.get("member_role_ids", []), "member"),
            }
            for group in app_request.requested_app_groups
        ]

        approver_id = approver.id if approver else None
        _ctx = get_request_context()
        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.app_request_approve,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": approver_id,
                    "current_user_email": approver.email if approver else None,
                    "app_request": app_request,
                    "requester": requester_user,
                    "service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        additional_app_groups: list[GroupDict] = [
            {"name": group["name"], "description": group["description"]} for group in app_groups
        ]
        created_app = await CreateApp(
            app=App(
                name=app_request.requested_app_name,
                description=app_request.requested_app_description,
                created_by_service_principal_id=app_request.requester_service_principal_id,
            ),
            tags=app_request.requested_app_tags,
            additional_app_groups=additional_app_groups,
            current_user_id=approver_id,
        ).execute()
        app_id = created_app.id

        groups_by_name = {
            g.name.lower(): g
            for g in (
                await db.session.scalars(
                    select(AppGroup).where(AppGroup.app_id == app_id).where(AppGroup.deleted_at.is_(None))
                )
            ).all()
        }
        owners_group = next(g for g in groups_by_name.values() if g.is_owner)
        owners_group_id = owners_group.id
        group_ids = {group["name"]: groups_by_name[group["name"].lower()].id for group in app_groups}

        created_reason = f"App request approved: {app_request.request_reason}"

        # The app's owners own and are members of its owners group.
        if owner_user_ids:
            await ModifyGroupUsers(
                group=owners_group_id,
                members_to_add=owner_user_ids,
                owners_to_add=owner_user_ids,
                users_added_ended_at=ownership_ending_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()
        for role_id in owner_role_ids:
            await ModifyRoleGroups(
                role_group=role_id,
                groups_to_add=[owners_group_id],
                owner_groups_to_add=[owners_group_id],
                groups_added_ended_at=ownership_ending_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()

        for group in app_groups:
            group_id = group_ids[group["name"]]
            if group["owner_user_ids"]:
                await ModifyGroupUsers(
                    group=group_id,
                    owners_to_add=group["owner_user_ids"],
                    users_added_ended_at=ownership_ending_at,
                    current_user_id=approver_id,
                    created_reason=created_reason,
                    notify=self.notify,
                ).execute()
            if group["member_user_ids"]:
                await ModifyGroupUsers(
                    group=group_id,
                    members_to_add=group["member_user_ids"],
                    users_added_ended_at=membership_ending_at,
                    current_user_id=approver_id,
                    created_reason=created_reason,
                    notify=self.notify,
                ).execute()
            for role_id in group["owner_role_ids"]:
                await ModifyRoleGroups(
                    role_group=role_id,
                    owner_groups_to_add=[group_id],
                    groups_added_ended_at=ownership_ending_at,
                    current_user_id=approver_id,
                    created_reason=created_reason,
                    notify=self.notify,
                ).execute()
            for role_id in group["member_role_ids"]:
                await ModifyRoleGroups(
                    role_group=role_id,
                    groups_to_add=[group_id],
                    groups_added_ended_at=membership_ending_at,
                    current_user_id=approver_id,
                    created_reason=created_reason,
                    notify=self.notify,
                ).execute()

        app_request = await db.session.get(AppRequest, self.app_request_id)
        assert app_request is not None
        app_request.status = AccessRequestStatus.APPROVED
        app_request.resolved_at = func.now()
        app_request.resolver_user_id = approver_id
        app_request.resolution_reason = self.approval_reason
        app_request.resolved_ownership_ending_at = ownership_ending_at
        app_request.resolved_membership_ending_at = membership_ending_at
        app_request.approved_app_id = app_id
        await db.session.commit()

        # A service principal learns the outcome by polling the request, so only a
        # user requester is notified.
        if self.notify and requester_user is not None:
            created_app = await db.session.get(App, app_id)
            approvers = await get_access_owners()
            await defer_notification(
                db.session,
                NotificationHook.ACCESS_APP_REQUEST_COMPLETED,
                detach=[app_request, created_app, requester_user, *approvers],
                app_request=app_request,
                app=created_app,
                requester=requester_user,
                approvers=approvers,
                notify_requester=True,
            )

        return app_request
