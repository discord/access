import datetime
import logging
import sys
from typing import Generator, Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, RoleRequest

notification_plugin_name = "access_notifications"
hookspec = pluggy.HookspecMarker(notification_plugin_name)
hookimpl = pluggy.HookimplMarker(notification_plugin_name)

_cached_notification_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)


class NotificationPluginSpec:
    @hookspec
    def access_request_created(
        self, access_request: AccessRequest, group: OktaGroup, requester: OktaUser, approvers: list[OktaUser]
    ) -> None:
        """Notify the approvers of the access request."""

    @hookspec
    def access_request_completed(
        self,
        access_request: AccessRequest,
        group: OktaGroup,
        requester: OktaUser,
        approvers: list[OktaUser],
        notify_requester: bool,
    ) -> None:
        """Notify the requester that their access request has been processed."""

    @hookspec(
        warn_on_impl_args={
            "groups": DeprecationWarning(
                "The groups parameter of access_expiring_user is deprecated and will be removed soon; "
                "use okta_user_group_members instead"
            ),
        },
    )
    def access_expiring_user(
        self,
        groups: list[OktaGroup],
        user: OktaUser,
        expiration_datetime: datetime.datetime,
        okta_user_group_members: Optional[list[OktaUserGroupMember]],
    ) -> None:
        """Notify individuals that their access to a group is expiring soon"""

    @hookspec(
        warn_on_impl_args={
            "groups": DeprecationWarning(
                "The groups parameter of access_expiring_owner is deprecated and will be removed soon; "
                "use group_user_associations and role_group_associations instead"
            ),
            "roles": DeprecationWarning(
                "The roles parameter of access_expiring_owner is deprecated and will be removed soon; "
                "use role_group_associations instead"
            ),
            "users": DeprecationWarning(
                "The users parameter of access_expiring_owner is deprecated and will be removed soon; "
                "use group_user_associations instead"
            ),
        },
    )
    def access_expiring_owner(
        self,
        owner: OktaUser,
        groups: list[OktaGroup],
        roles: list[RoleGroup],
        users: list[OktaUser],
        expiration_datetime: datetime.datetime,
        group_user_associations: Optional[list[OktaUserGroupMember]],
        role_group_associations: Optional[list[RoleGroupMap]],
    ) -> None:
        """Notify group owners that individuals or roles access to a group is expiring soon"""

    @hookspec
    def access_expiring_role_owner(
        self,
        owner: OktaUser,
        roles: list[RoleGroupMap],
        expiration_datetime: datetime.datetime,
    ) -> None:
        """Notify role owners that roles they own will be losing access soon"""

    @hookspec
    def access_role_request_created(
        self,
        role_request: RoleRequest,
        role: RoleGroup,
        group: OktaGroup,
        requester: OktaUser,
        approvers: list[OktaUser],
    ) -> None:
        """Notify the approvers of the role request."""

    @hookspec
    def access_role_request_completed(
        self,
        role_request: RoleRequest,
        role: RoleGroup,
        group: OktaGroup,
        requester: OktaUser,
        approvers: list[OktaUser],
        notify_requester: bool,
    ) -> None:
        """Notify the requester that their role request has been processed."""


@hookimpl(wrapper=True)
def access_request_created(
    access_request: AccessRequest, group: OktaGroup, requester: OktaUser, approvers: list[OktaUser]
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute access request created notification callback")


@hookimpl(wrapper=True)
def access_request_completed(
    access_request: AccessRequest,
    group: OktaGroup,
    requester: OktaUser,
    approvers: list[OktaUser],
    notify_requester: bool,
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute access request completed notification callback")


@hookimpl(wrapper=True)
def access_expiring_user(
    groups: list[OktaGroup],
    user: OktaUser,
    expiration_datetime: datetime.datetime,
    okta_user_group_members: Optional[list[OktaUserGroupMember]],
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute access expiring for user notification callback")


@hookimpl(wrapper=True)
def access_expiring_owner(
    owner: OktaUser,
    groups: list[OktaGroup],
    roles: list[RoleGroup],
    users: list[OktaUser],
    expiration_datetime: datetime.datetime,
    group_user_associations: Optional[list[OktaUserGroupMember]],
    role_group_associations: Optional[list[RoleGroupMap]],
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute access expiring for owner notification callback")


@hookimpl(wrapper=True)
def access_expiring_role_owner(
    owner: OktaUser,
    roles: list[RoleGroupMap],
    expiration_datetime: datetime.datetime,
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute access expiring for role owner notification callback")


@hookimpl(wrapper=True)
def access_role_request_created(
    role_request: RoleRequest,
    role: RoleGroup,
    group: OktaGroup,
    requester: OktaUser,
    approvers: list[OktaUser],
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute role request created notification callback")


@hookimpl(wrapper=True)
def access_role_request_completed(
    role_request: RoleRequest,
    role: RoleGroup,
    group: OktaGroup,
    requester: OktaUser,
    approvers: list[OktaUser],
    notify_requester: bool,
) -> Generator[None, None, None]:
    try:
        return (yield)
    except Exception:
        # Log and do not raise since notification failures should not
        # break the flow. Users can still manually ping approvers
        # to process their request from the UI
        logger.exception("Failed to execute role request completed notification callback")


def get_notification_hook() -> pluggy.HookRelay:
    global _cached_notification_hook

    if _cached_notification_hook is not None:
        return _cached_notification_hook

    pm = pluggy.PluginManager(notification_plugin_name)
    pm.add_hookspecs(NotificationPluginSpec)

    # Register the hook wrappers
    pm.register(sys.modules[__name__])

    count = pm.load_setuptools_entrypoints(notification_plugin_name)
    print(f"Count of loaded notification plugins: {count}")
    _cached_notification_hook = pm.hook

    return _cached_notification_hook
