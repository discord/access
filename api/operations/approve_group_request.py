import copy
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectin_polymorphic

from api.context import get_request_context
from api.exceptions import ConflictError, InvalidRequestError
from api.extensions import db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    GroupRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.models.app_group import get_access_owners
from api.models.okta_group import is_group_name_taken
from api.models.tag import effective_ended_at
from api.operations._active_ids import granted_access, require_active_role_ids, require_active_user_ids
from api.operations._ending_at import USE_REQUESTED, EndingAtChoice, choose_ending_at
from api.operations._fan_out import defer_notification
from api.operations._requesters import load_active_requester, split_requester
from api.operations._service_principals import audit_ref
from api.operations.constraints.check_group_request_self_add import (
    CheckGroupRequestSelfAdd,
    missing_reason_message,
    new_group_tags,
)
from api.operations.create_group import CreateGroup
from api.operations.modify_group_users import ModifyGroupUsers
from api.operations.modify_role_groups import ModifyRoleGroups
from api.plugins import NotificationHook
from api.routers._eager import effective_constraint_options
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


class ApproveGroupRequest:
    """Approve a pending group request: create the group and grant its owners and members.

    Args:
        group_request: The request or its id.
        approver_user: The approving user, or None for a conditional-access plugin approval.
        approval_reason: Recorded as the resolution reason.
        ending_at: Caps the owner grants; it never extends them.
        membership_ending_at: Caps the member grants; it never extends them.
        approver_ownership_ending_at: The approver's end date for the owner grants, replacing the
            requested one (None for indefinite), or `USE_REQUESTED` to keep it.
        approver_membership_ending_at: The same for the member grants.
        notify: Whether to send notifications.
        bypass_self_approval: Let the requester approve their own request (app-owner auto-approval).
            Grants the group's tags forbid the approver from adding to themself are skipped
            instead of failing the approval.
        owner_user_ids: Replaces the requested owner users, or None to keep them.
        owner_role_ids: Replaces the requested owner roles, or None to keep them.
        member_user_ids: Replaces the requested member users, or None to keep them.
        member_role_ids: Replaces the requested member roles, or None to keep them.
    """

    def __init__(
        self,
        *,
        group_request: GroupRequest | str,
        approver_user: OktaUser | str | None = None,
        approval_reason: str = "",
        ending_at: datetime | None = None,
        membership_ending_at: datetime | None = None,
        approver_ownership_ending_at: EndingAtChoice = USE_REQUESTED,
        approver_membership_ending_at: EndingAtChoice = USE_REQUESTED,
        notify: bool = True,
        bypass_self_approval: bool = False,
        owner_user_ids: list[str] | None = None,
        owner_role_ids: list[str] | None = None,
        member_user_ids: list[str] | None = None,
        member_role_ids: list[str] | None = None,
    ):
        self.group_request_id = group_request if isinstance(group_request, str) else group_request.id
        self.approver_user_id = (
            approver_user.id if approver_user is not None and not isinstance(approver_user, str) else approver_user
        )

        self.approval_reason = approval_reason
        self.ending_at = ending_at
        self.membership_ending_at = membership_ending_at
        self.approver_ownership_ending_at = approver_ownership_ending_at
        self.approver_membership_ending_at = approver_membership_ending_at
        self.notify = notify
        self.bypass_self_approval = bypass_self_approval
        self.owner_user_ids = owner_user_ids
        self.owner_role_ids = owner_role_ids
        self.member_user_ids = member_user_ids
        self.member_role_ids = member_role_ids

    async def execute(self) -> GroupRequest | None:
        # Lock the request row for the transaction so concurrent approvers
        # can't both pass the pending-state guard and double-create the group.
        # `of=` keeps FOR UPDATE off the joinedload's nullable outer-join side
        # (Postgres rejects that); no-op on SQLite.
        group_request = (
            await db.session.scalars(
                select(GroupRequest)
                .options(joinedload(GroupRequest.active_requester))
                .where(GroupRequest.id == self.group_request_id)
                .with_for_update(of=GroupRequest)
            )
        ).first()

        approver_id: str | None = None
        if self.approver_user_id is None:
            approver_email = None
        else:
            approver = await db.session.get(OktaUser, self.approver_user_id)
            assert approver is not None
            approver_id = approver.id
            approver_email = approver.email

        # Guard against missing group_request
        if group_request is None:
            return None

        # Don't allow approving a request that is already resolved. Raise
        # rather than silently no-op so a stale/concurrent approval surfaces
        # as a conflict instead of looking like a success.
        if group_request.status != AccessRequestStatus.PENDING or group_request.resolved_at is not None:
            raise ConflictError("Group request is no longer pending")

        # Don't allow requester to approve their own request
        if approver_id is not None and group_request.requester_user_id == approver_id and not self.bypass_self_approval:
            return group_request

        # Don't allow approving a request whose requester is deleted.
        requester = await load_active_requester(group_request.requester_id)
        if requester is None:
            return group_request
        requester_user, requester_service_principal = split_requester(requester)

        # Resolve group fields: use resolved_* if set, otherwise fall back to requested_*
        resolved_name = (
            group_request.resolved_group_name
            if group_request.resolved_group_name
            else group_request.requested_group_name
        )
        resolved_description = (
            group_request.resolved_group_description
            if group_request.resolved_group_description
            else group_request.requested_group_description
        )
        resolved_type = (
            group_request.resolved_group_type
            if group_request.resolved_group_type
            else group_request.requested_group_type
        )
        resolved_app_id = (
            group_request.resolved_app_id if group_request.resolved_app_id else group_request.requested_app_id
        )
        resolved_tags = (
            group_request.resolved_group_tags
            if group_request.resolved_group_tags
            else group_request.requested_group_tags
        )
        resolved_plugin_data = (
            group_request.resolved_plugin_data
            if group_request.resolved_plugin_data
            else group_request.requested_plugin_data
        )

        # Resolved here, before anything is created, so they can be validated. A
        # resolved value already on the request (set by an earlier caller) stands in
        # for the requested one; the approver's choice replaces either, and a
        # conditional-access plugin's `ending_at` / `membership_ending_at` only ever
        # shorten the result.
        resolved_ownership_ending_at = choose_ending_at(
            group_request.resolved_ownership_ending_at or group_request.requested_ownership_ending_at,
            self.approver_ownership_ending_at,
            self.ending_at,
        )
        resolved_membership_ending_at = choose_ending_at(
            group_request.resolved_membership_ending_at or group_request.requested_membership_ending_at,
            self.approver_membership_ending_at,
            self.membership_ending_at,
        )
        # Refuse to approve into an ownership window that has already closed.
        # Nothing downstream clamps it: effective_ended_at only ever moves the date
        # earlier, so a past value reaches ModifyGroupUsers and writes an ownership
        # row that is expired on arrival. get_group_managers filters on ended_at, so
        # the new group would have no owners at all, and its future approvals would
        # silently route to the app owner or an Access admin instead of the person
        # who asked for it.
        #
        # This is reachable three ways, which is why the guard lives here rather
        # than in the router: any client that leaves the requested (already past)
        # date in place, an approver choosing a past custom date, or a
        # conditional-access plugin returning a past `ending_at`. Failing loudly
        # makes the approver name a window they mean, which is the only safe
        # default: the alternatives silently grant either nothing or more than was asked.
        if resolved_ownership_ending_at is not None and resolved_ownership_ending_at <= datetime.now(UTC):
            raise InvalidRequestError(
                "The ownership window for this request has already passed. Set an ownership end date when approving it."
            )

        # authorization: no approver means a conditional-access plugin approved the
        # request (see `ConditionalAccessPluginSpec.group_request_created`), and that
        # operator-installed plugin stands in for the approval tiers below. The resolve
        # route always passes the calling user, so a person never reaches this path.
        access_owner_ids = {u.id for u in await get_access_owners()}
        may_approve_any = approver_id is None or approver_id in access_owner_ids

        if not may_approve_any:
            type_changed = resolved_type != group_request.requested_group_type
            app_changed = resolved_app_id != group_request.requested_app_id
            if type_changed or app_changed:
                return group_request

        if resolved_app_id is not None:
            # App group request: admins OR owners of that specific app can approve
            if not may_approve_any:
                is_app_owner = (
                    await db.session.scalars(
                        select(OktaUserGroupMember)
                        .join(AppGroup, OktaUserGroupMember.group_id == AppGroup.id)
                        .where(
                            AppGroup.app_id == resolved_app_id,
                            AppGroup.is_owner.is_(True),
                            AppGroup.deleted_at.is_(None),
                            OktaUserGroupMember.user_id == approver_id,
                            OktaUserGroupMember.is_owner.is_(True),
                            or_(
                                OktaUserGroupMember.ended_at.is_(None),
                                OktaUserGroupMember.ended_at > func.now(),
                            ),
                        )
                    )
                ).first()

                if not is_app_owner:
                    return group_request
        else:
            # okta_group / role_group request: only admins can approve
            if not may_approve_any:
                return group_request

        # Validation
        if resolved_type != "app_group" and resolved_name.startswith(AppGroup.APP_GROUP_NAME_PREFIX):
            return group_request

        if resolved_type != "role_group" and resolved_name.startswith(RoleGroup.ROLE_GROUP_NAME_PREFIX):
            return group_request

        if resolved_type == "app_group" and resolved_name.endswith(
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ):
            return group_request

        owner_user_ids = await require_active_user_ids(
            self.owner_user_ids if self.owner_user_ids is not None else group_request.requested_owner_user_ids,
            "owner",
        )
        owner_role_ids = await require_active_role_ids(
            self.owner_role_ids if self.owner_role_ids is not None else group_request.requested_owner_role_ids,
            "owner",
        )
        member_user_ids = await require_active_user_ids(
            self.member_user_ids if self.member_user_ids is not None else group_request.requested_member_user_ids,
            "member",
        )
        member_role_ids = await require_active_role_ids(
            self.member_role_ids if self.member_role_ids is not None else group_request.requested_member_role_ids,
            "member",
        )
        if resolved_type == "role_group" and (owner_role_ids or member_role_ids):
            raise InvalidRequestError("Roles cannot be added to other Roles")
        # A request without members grants no membership, so it has no membership window.
        has_members = bool(member_user_ids or member_role_ids)
        if not has_members:
            resolved_membership_ending_at = None
        elif resolved_membership_ending_at is not None and resolved_membership_ending_at <= datetime.now(UTC):
            raise InvalidRequestError(
                "The membership window for this request has already passed. "
                "Set a membership end date when approving it."
            )

        # Hold the approver to the new group's self-add restrictions, as a direct add
        # would. App-owner auto-approval skips the blocked grants instead: the
        # requester already manages the group through the app.
        reason_error = missing_reason_message(
            await new_group_tags(resolved_tags, resolved_app_id if resolved_type == "app_group" else None),
            group_request.request_reason,
            grants_ownership=bool(owner_user_ids or owner_role_ids),
            grants_membership=has_members,
        )
        if reason_error is not None:
            raise InvalidRequestError(reason_error)

        self_add = await CheckGroupRequestSelfAdd(
            approver_id=approver_id,
            tag_ids=resolved_tags,
            app_id=resolved_app_id if resolved_type == "app_group" else None,
            owner_user_ids=owner_user_ids,
            member_user_ids=member_user_ids,
            owner_role_ids=owner_role_ids,
            member_role_ids=member_role_ids,
        ).execute()
        if self_add.blocked:
            if not self.bypass_self_approval:
                raise InvalidRequestError(self_add.message)
            if self_add.owner_user:
                owner_user_ids = [u for u in owner_user_ids if u != approver_id]
            if self_add.member_user:
                member_user_ids = [u for u in member_user_ids if u != approver_id]
            owner_role_ids = [r for r in owner_role_ids if r not in self_add.owner_role_ids]
            member_role_ids = [r for r in member_role_ids if r not in self_add.member_role_ids]

        # Raise rather than leave the request pending: otherwise the approver sees a
        # successful response for an approval that created nothing.
        if await is_group_name_taken(resolved_name):
            raise ConflictError(
                f"A group named '{resolved_name}' already exists. Reject this request, "
                "or approve it under a different name."
            )

        # Resolve the plugin config the new group will carry, and re-validate it here
        # with the rest of the validation: the app or its plugin config may have
        # changed since the request was filed, so this can legitimately fail. Raising
        # after the group_request_approve audit event below would leave an "approved"
        # audit entry for an approval that never completed — re-emitted on every retry.
        new_group_plugin_data: dict[str, Any] = {}
        if resolved_type == "app_group" and resolved_plugin_data:
            resolved_app = await db.session.get(App, resolved_app_id)
            plugin_id = resolved_app.app_group_lifecycle_plugin if resolved_app is not None else None
            if plugin_id is None:
                logger.warning(
                    f"Group request {group_request.id} carried plugin_data, "
                    f"but app {resolved_app_id} has no app_group_lifecycle_plugin; "
                    "dropping the supplied config."
                )
            else:
                from api.plugins.app_group_lifecycle import (
                    validate_app_group_lifecycle_plugin_group_config,
                )

                plugin_errors = validate_app_group_lifecycle_plugin_group_config(
                    resolved_plugin_data,
                    plugin_id,
                )
                if plugin_errors:
                    raise ValueError(f"plugin_data: {plugin_errors}")
                # Deep-copy so the created group and the persisted request don't share
                # nested plugin_data objects: the group_created hook may mutate the
                # group's copy (e.g. writing status), which must not leak back into the
                # immutable request record.
                new_group_plugin_data = copy.deepcopy(resolved_plugin_data)

        await db.session.commit()

        # Audit logging
        _ctx = get_request_context()

        logging.getLogger("access.audit").info(
            AuditLogSchema().dumps(
                {
                    "event_type": EventType.group_request_approve,
                    "user_agent": _ctx.user_agent if _ctx else None,
                    "ip": _ctx.ip if _ctx else None,
                    "current_user_id": approver_id,
                    "current_user_email": approver_email,
                    "group_request": group_request,
                    "requester": requester_user,
                    "requester_service_principal": audit_ref(requester_service_principal),
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
            # Carry the config resolved and validated above onto the group so the
            # group_created hook (fired inside CreateGroup) sees it.
            if new_group_plugin_data:
                new_group.plugin_data = new_group_plugin_data
        else:
            new_group = OktaGroup(
                name=resolved_name,
                description=resolved_description,
            )

        new_group.created_by_service_principal_id = group_request.requester_service_principal_id

        created_group: AppGroup | OktaGroup | RoleGroup = await CreateGroup(
            group=new_group,
            tags=resolved_tags,
            current_user_id=approver_id,
        ).execute()

        # Check tags on created group for ownership length constraints including propagated app tags
        created_group_with_tags = (
            await db.session.scalars(
                select(OktaGroup)
                .options(
                    selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
                    *effective_constraint_options(),
                )
                .where(OktaGroup.id == created_group.id)
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).first()
        assert created_group_with_tags is not None

        coalesced_ownership_ending_at = effective_ended_at(
            Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY, created_group_with_tags, resolved_ownership_ending_at
        )
        coalesced_membership_ending_at = (
            effective_ended_at(
                Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY, created_group_with_tags, resolved_membership_ending_at
            )
            if has_members
            else None
        )

        # Record what the approval grants.
        group_request.resolved_ownership_ending_at = coalesced_ownership_ending_at
        group_request.resolved_membership_ending_at = coalesced_membership_ending_at

        created_reason = f"Group request approved: {group_request.request_reason}"
        if owner_user_ids:
            await ModifyGroupUsers(
                group=created_group_with_tags,
                owners_to_add=owner_user_ids,
                users_added_ended_at=coalesced_ownership_ending_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()
        if member_user_ids:
            await ModifyGroupUsers(
                group=created_group_with_tags,
                members_to_add=member_user_ids,
                users_added_ended_at=coalesced_membership_ending_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()
        for role_id in owner_role_ids:
            await ModifyRoleGroups(
                role_group=role_id,
                owner_groups_to_add=[created_group.id],
                groups_added_ended_at=coalesced_ownership_ending_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()
        for role_id in member_role_ids:
            await ModifyRoleGroups(
                role_group=role_id,
                groups_to_add=[created_group.id],
                groups_added_ended_at=coalesced_membership_ending_at,
                current_user_id=approver_id,
                created_reason=created_reason,
                notify=self.notify,
            ).execute()

        # Record the grants that were made, which a grant operation may have
        # skipped, rather than the ones that were asked for.
        granted = await granted_access(created_group.id)
        group_request.resolved_owner_user_ids = [u for u in owner_user_ids if u in granted.owner_user_ids]
        group_request.resolved_owner_role_ids = [r for r in owner_role_ids if r in granted.owner_role_ids]
        group_request.resolved_member_user_ids = [u for u in member_user_ids if u in granted.member_user_ids]
        group_request.resolved_member_role_ids = [r for r in member_role_ids if r in granted.member_role_ids]

        group_request.status = AccessRequestStatus.APPROVED
        group_request.resolved_at = func.now()
        group_request.resolver_user_id = approver_id
        group_request.resolution_reason = self.approval_reason
        group_request.approved_group_id = created_group.id

        await db.session.commit()

        # A service principal learns the outcome by polling the request, so only a
        # user requester is notified.
        if self.notify and group_request.requester_user_id is not None:
            requester = await db.session.get(OktaUser, group_request.requester_user_id)
            approvers = await get_all_possible_request_approvers(group_request)
            await defer_notification(
                db.session,
                NotificationHook.ACCESS_GROUP_REQUEST_COMPLETED,
                detach=[group_request, created_group, requester, *approvers],
                group_request=group_request,
                group=created_group,
                requester=requester,
                approvers=approvers,
                notify_requester=True,
            )

        return group_request
