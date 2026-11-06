import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select

from api.context import get_request_context
from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import AccessRequestStatus, App, AppGroup, AppRequest, OktaGroup, OktaUser, Tag
from api.models.app_group import get_access_owners
from api.models.tag import coalesce_ended_at
from api.operations._active_ids import partition_active_role_ids, partition_active_user_ids
from api.operations._fan_out import defer_notification
from api.operations._grants import carry_out_grants, failed_grants_note
from api.operations._requesters import load_active_requester, split_requester
from api.operations._service_principals import audit_ref
from api.operations.create_app import CreateApp, owner_group_conflict
from api.operations.create_group import GroupDict
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_role_groups import ModifyRoleGroups
from api.plugins import NotificationHook
from api.schemas import AuditLogSchema, EventType
from api.schemas.app_requests import AppRequestGroup

logger = logging.getLogger(__name__)


def _capped(requested: datetime | None, cap: datetime | None) -> datetime | None:
    """`requested`, shortened to `cap` when `cap` is earlier; a cap never extends it."""
    if requested is not None:
        requested = requested.replace(tzinfo=UTC) if requested.tzinfo is None else requested.astimezone(UTC)
    if cap is None:
        return requested
    cap = cap.replace(tzinfo=UTC) if cap.tzinfo is None else cap.astimezone(UTC)
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
        approver_ownership_ending_at: The approver's end date for the owner grants, replacing the
            requested one, or None to keep it.
        approver_membership_ending_at: The same for the member grants.
        notify: Whether to send notifications.

    Raises:
        ConflictError: The request is no longer pending, or the app can't be created
            under its name.
        InvalidRequestError: An end date has passed, or none of the named app owners is
            still active. Other named users and roles that are no longer active are
            skipped and noted in the resolution reason.
    """

    def __init__(
        self,
        *,
        app_request: AppRequest | str,
        approver_user: OktaUser | str | None = None,
        approval_reason: str = "",
        ending_at: datetime | None = None,
        membership_ending_at: datetime | None = None,
        approver_ownership_ending_at: datetime | None = None,
        approver_membership_ending_at: datetime | None = None,
        notify: bool = True,
    ):
        self.app_request_id = app_request if isinstance(app_request, str) else app_request.id
        self.approver_user_id = approver_user if not isinstance(approver_user, OktaUser) else approver_user.id
        self.approval_reason = approval_reason
        self.ending_at = ending_at
        self.membership_ending_at = membership_ending_at
        self.approver_ownership_ending_at = approver_ownership_ending_at
        self.approver_membership_ending_at = approver_membership_ending_at
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
            if app_request.is_requested_by(approver.id):
                return app_request

        requester = await load_active_requester(app_request.requester_id)
        if requester is None:
            return app_request
        requester_user, requester_service_principal = split_requester(requester)

        app_groups = [AppRequestGroup.model_validate(group) for group in app_request.requested_app_groups]
        has_members = any(group.member_user_ids or group.member_role_ids for group in app_groups)
        tags = list(
            (
                await db.session.scalars(
                    select(Tag).where(Tag.id.in_(app_request.requested_app_tags)).where(Tag.deleted_at.is_(None))
                )
            ).all()
        )

        # Every group of a new app carries exactly the app's tags, so the tag limits
        # capping each grant are known before anything is created.
        ownership_ending_at = coalesce_ended_at(
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags,
            _capped(self.approver_ownership_ending_at or app_request.requested_ownership_ending_at, self.ending_at),
            group_is_managed=True,
        )
        membership_ending_at = (
            coalesce_ended_at(
                Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY,
                tags,
                _capped(
                    self.approver_membership_ending_at or app_request.requested_membership_ending_at,
                    self.membership_ending_at,
                ),
                group_is_managed=True,
            )
            if has_members
            else None
        )
        now = datetime.now(UTC)
        if ownership_ending_at is not None and ownership_ending_at.replace(tzinfo=UTC) <= now:
            raise InvalidRequestError(
                "The ownership window for this request has already passed. Set an ownership end date when approving it."
            )
        if membership_ending_at is not None and membership_ending_at.replace(tzinfo=UTC) <= now:
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

        # Users and roles named in the request may have been deleted since. They are
        # skipped and the resolution reason says so, rather than leaving a request
        # nobody can approve; the app still needs one active owner.
        departed: list[str] = []

        async def active_users(ids: list[str]) -> list[str]:
            active, gone = await partition_active_user_ids(ids)
            departed.extend(f"user {u}" for u in gone)
            return active

        async def active_roles(ids: list[str]) -> list[str]:
            active, gone = await partition_active_role_ids(ids)
            departed.extend(f"role {r}" for r in gone)
            return active

        owner_user_ids = await active_users(app_request.requested_owner_user_ids)
        owner_role_ids = await active_roles(app_request.requested_owner_role_ids)
        if not owner_user_ids and not owner_role_ids:
            raise InvalidRequestError("None of the app owners this request names is still active")
        app_groups = [
            group.model_copy(
                update={
                    "owner_user_ids": await active_users(group.owner_user_ids),
                    "owner_role_ids": await active_roles(group.owner_role_ids),
                    "member_user_ids": await active_users(group.member_user_ids),
                    "member_role_ids": await active_roles(group.member_role_ids),
                }
            )
            for group in app_groups
        ]

        prefix = (
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_request.requested_app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        )
        # Creating the app absorbs any existing groups already carrying its prefix;
        # only the groups it creates are credited to the requesting service principal.
        preexisting_group_ids = set(
            (
                await db.session.scalars(
                    select(OktaGroup.id)
                    .where(OktaGroup.name.istartswith(prefix, autoescape=True))
                    .where(OktaGroup.deleted_at.is_(None))
                )
            ).all()
        )

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
                    "requester_service_principal": audit_ref(requester_service_principal),
                }
            )
        )

        additional_app_groups: list[GroupDict] = [
            {"name": group.name, "description": group.description} for group in app_groups
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

        app_groups_by_name = {
            g.name.lower(): g
            for g in (
                await db.session.scalars(
                    select(AppGroup).where(AppGroup.app_id == app_id).where(AppGroup.deleted_at.is_(None))
                )
            ).all()
        }
        owners_group = next(g for g in app_groups_by_name.values() if g.is_owner)
        owners_group_id = owners_group.id
        group_ids = {group.name: app_groups_by_name[group.name.lower()].id for group in app_groups}
        if app_request.requester_service_principal_id is not None:
            for created in app_groups_by_name.values():
                if created.id not in preexisting_group_ids:
                    created.created_by_service_principal_id = app_request.requester_service_principal_id
            await db.session.commit()

        created_reason = f"App request approved: {app_request.request_reason}"

        def add_users(
            group_id: str, owners: list[str], members: list[str], ended_at: datetime | None
        ) -> Callable[[], Awaitable[object]]:
            return lambda: ModifyGroupUsers(
                group=group_id,
                owners_to_add=owners,
                members_to_add=members,
                users_added_ended_at=ended_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()

        def add_role(
            role_id: str, group_id: str, owner: bool, member: bool, ended_at: datetime | None
        ) -> Callable[[], Awaitable[object]]:
            return lambda: ModifyRoleGroups(
                role_group=role_id,
                owner_groups_to_add=[group_id] if owner else [],
                groups_to_add=[group_id] if member else [],
                groups_added_ended_at=ended_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()

        # The app's owners own and are members of its owners group.
        grants: list[tuple[str, Callable[[], Awaitable[object]]]] = []
        if owner_user_ids:
            grants.append(
                (
                    f"app owners {', '.join(owner_user_ids)}",
                    add_users(owners_group_id, owner_user_ids, owner_user_ids, ownership_ending_at),
                )
            )
        grants += [
            (f"app owner role {r}", add_role(r, owners_group_id, True, True, ownership_ending_at))
            for r in owner_role_ids
        ]
        for group in app_groups:
            group_id = group_ids[group.name]
            if group.owner_user_ids:
                grants.append(
                    (
                        f"{group.name} owners {', '.join(group.owner_user_ids)}",
                        add_users(group_id, group.owner_user_ids, [], ownership_ending_at),
                    )
                )
            if group.member_user_ids:
                grants.append(
                    (
                        f"{group.name} members {', '.join(group.member_user_ids)}",
                        add_users(group_id, [], group.member_user_ids, membership_ending_at),
                    )
                )
            grants += [
                (f"{group.name} owner role {r}", add_role(r, group_id, True, False, ownership_ending_at))
                for r in group.owner_role_ids
            ]
            grants += [
                (f"{group.name} member role {r}", add_role(r, group_id, False, True, membership_ending_at))
                for r in group.member_role_ids
            ]
        failed = await carry_out_grants(grants, f"app request {self.app_request_id}")

        app_request = await db.session.get(AppRequest, self.app_request_id)
        assert app_request is not None
        app_request.status = AccessRequestStatus.APPROVED
        app_request.resolved_at = func.now()
        app_request.resolver_user_id = approver_id
        app_request.resolution_reason = (
            self.approval_reason
            + (f" (Skipped, no longer active: {'; '.join(departed)})" if departed else "")
            + failed_grants_note(failed)
        )
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
