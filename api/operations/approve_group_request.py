import copy
import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.context import get_request_context
from api.exceptions import ConflictError
from api.extensions import db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    GroupRequest,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    Tag,
)
from api.models.access_request import get_all_possible_request_approvers
from api.models.app_group import get_access_owners
from api.models.tag import coalesce_ended_at
from api.operations._fan_out import defer_notification
from api.operations.constraints.check_for_self_add import CheckForSelfAdd
from api.operations.create_group import CreateGroup
from api.operations.modify_group_users import ModifyGroupUsers
from api.plugins import NotificationHook
from api.schemas import AuditLogSchema, EventType

logger = logging.getLogger(__name__)


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
        self.group_request_id = group_request if isinstance(group_request, str) else group_request.id
        self.approver_user_id = (
            approver_user.id if approver_user is not None and not isinstance(approver_user, str) else approver_user
        )

        self.approval_reason = approval_reason
        self.notify = notify
        self.bypass_self_approval = bypass_self_approval

    async def execute(self) -> Optional[GroupRequest]:
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
        if group_request.requester_user_id == approver_id and not self.bypass_self_approval:
            return group_request

        # Don't allow approving a request if the requester is deleted. (Note:
        # a deleted requester usually can't even load above — active_requester
        # is an inner join filtering deleted_at — so this is a belt-and-braces
        # check that mirrors the historical no-op rather than a 4xx path.)
        requester = await db.session.get(OktaUser, group_request.requester_user_id)
        if requester is None or requester.deleted_at is not None:
            return group_request

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

        # authorization
        access_owner_ids = {u.id for u in await get_access_owners()}
        is_admin = approver_id in access_owner_ids

        if not is_admin:
            type_changed = resolved_type != group_request.requested_group_type
            app_changed = resolved_app_id != group_request.requested_app_id
            if type_changed or app_changed:
                return group_request

        if resolved_app_id is not None:
            # App group request: admins OR owners of that specific app can approve
            if not is_admin:
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
                            OktaUserGroupMember.ended_at.is_(None),
                        )
                    )
                ).first()

                if not is_app_owner:
                    return group_request
        else:
            # okta_group / role_group request: only admins can approve
            if not is_admin:
                return group_request

        if resolved_type != "app_group" and resolved_name.startswith(AppGroup.APP_GROUP_NAME_PREFIX):
            return group_request

        if resolved_type != "role_group" and resolved_name.startswith(RoleGroup.ROLE_GROUP_NAME_PREFIX):
            return group_request

        if resolved_type == "app_group" and resolved_name.endswith(
            f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        ):
            return group_request

        existing_group = (
            await db.session.scalars(
                select(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
                .where(func.lower(OktaGroup.name) == func.lower(resolved_name))
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).first()
        if existing_group is not None:
            return group_request

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
                    "requester": await db.session.get(OktaUser, group_request.requester_user_id),
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
            # Carry the request's plugin config onto the group so the
            # group_created hook (fired inside CreateGroup) sees it. Re-validate
            # defensively: the app or its config may have changed since filing.
            if resolved_plugin_data:
                resolved_app = await db.session.get(App, resolved_app_id)
                plugin_id = resolved_app.app_group_lifecycle_plugin if resolved_app is not None else None
                if plugin_id is not None:
                    from api.plugins.app_group_lifecycle import (
                        validate_app_group_lifecycle_plugin_group_config,
                    )

                    plugin_errors = validate_app_group_lifecycle_plugin_group_config(
                        resolved_plugin_data,
                        plugin_id,
                    )
                    if plugin_errors:
                        raise ValueError(f"plugin_data: {plugin_errors}")
                    # Deep-copy so the created group and the persisted request
                    # don't share nested plugin_data objects: the group_created
                    # hook may mutate the group's copy (e.g. writing status),
                    # which must not leak back into the immutable request record.
                    new_group.plugin_data = copy.deepcopy(resolved_plugin_data)
                else:
                    logger.warning(
                        f"Group request {group_request.id} carried plugin_data, "
                        f"but app {resolved_app_id} has no app_group_lifecycle_plugin; "
                        "dropping the supplied config."
                    )
        else:
            new_group = OktaGroup(
                name=resolved_name,
                description=resolved_description,
            )

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
                    selectinload(OktaGroup.active_group_tags).joinedload(OktaGroupTagMap.active_tag),
                )
                .where(OktaGroup.id == created_group.id)
                .where(OktaGroup.deleted_at.is_(None))
            )
        ).first()

        tags = [tag_map.active_tag for tag_map in created_group_with_tags.active_group_tags]

        # Determine the initial ending time: prefer resolved, fall back to requested
        initial_ending_at = (
            group_request.resolved_ownership_ending_at
            if group_request.resolved_ownership_ending_at
            else group_request.requested_ownership_ending_at
        )

        coalesced_ownership_ending_at = coalesce_ended_at(
            constraint_key=Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY,
            tags=tags,
            initial_ended_at=initial_ending_at,
            group_is_managed=True,
        )

        # Update the group request with the coalesced value to ensure consistency
        group_request.resolved_ownership_ending_at = coalesced_ownership_ending_at

        # Add the requester as an owner of the newly created group
        # If app owner auto approval, skip if group has owner add constraint (will inherit ownership via app)
        can_add_owner = True
        if self.bypass_self_approval and approver_id is not None:
            can_add_owner, _ = await CheckForSelfAdd(
                group=created_group_with_tags, current_user=approver_id, owners_to_add=[approver_id]
            ).execute_for_group()

        if can_add_owner:
            await ModifyGroupUsers(
                group=created_group_with_tags,
                owners_to_add=[group_request.requester_user_id],
                users_added_ended_at=coalesced_ownership_ending_at,
                current_user_id=approver_id,
                created_reason=f"Group request approved: {group_request.request_reason}",
                notify=self.notify,
            ).execute()

        group_request.status = AccessRequestStatus.APPROVED
        group_request.resolved_at = func.now()
        group_request.resolver_user_id = approver_id
        group_request.resolution_reason = self.approval_reason
        group_request.approved_group_id = created_group.id

        await db.session.commit()

        if self.notify:
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
