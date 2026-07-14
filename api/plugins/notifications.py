import datetime
import logging
from enum import StrEnum
from typing import Any, Dict, Optional

import pluggy

from api.models import (
    AccessRequest,
    GroupRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
)
from api.plugins._async_dispatch import run_hooks_to_completion, verify_async_impls
from api.plugins.metrics_reporter import get_metrics_reporter_hook

notification_plugin_name = "access_notifications"
hookspec = pluggy.HookspecMarker(notification_plugin_name)
hookimpl = pluggy.HookimplMarker(notification_plugin_name)

_cached_notification_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)


class NotificationHook(StrEnum):
    """Notification hook names. Each value is the pluggy hook attribute name, so a
    member can be passed straight to ``getattr(hook_relay, member)``."""

    ACCESS_REQUEST_CREATED = "access_request_created"
    ACCESS_REQUEST_COMPLETED = "access_request_completed"
    ACCESS_EXPIRING_USER = "access_expiring_user"
    ACCESS_EXPIRING_OWNER = "access_expiring_owner"
    ACCESS_EXPIRING_ROLE_OWNER = "access_expiring_role_owner"
    ACCESS_ROLE_REQUEST_CREATED = "access_role_request_created"
    ACCESS_ROLE_REQUEST_COMPLETED = "access_role_request_completed"
    ACCESS_GROUP_REQUEST_CREATED = "access_group_request_created"
    ACCESS_GROUP_REQUEST_COMPLETED = "access_group_request_completed"


# hook -> (metric name, static tags) recorded once the hook fans out
# successfully. Kept here (not at call sites) so the "sent" accounting lives
# next to the spec it measures.
_SENT_METRICS: dict[NotificationHook, tuple[str, Optional[Dict[str, str]]]] = {
    NotificationHook.ACCESS_REQUEST_CREATED: ("notifications.access_request_created.sent", None),
    NotificationHook.ACCESS_REQUEST_COMPLETED: ("notifications.access_request_completed.sent", None),
    NotificationHook.ACCESS_EXPIRING_USER: ("notifications.expiring_access.sent", {"kind": "user"}),
    NotificationHook.ACCESS_EXPIRING_OWNER: ("notifications.expiring_access.sent", {"kind": "owner"}),
    NotificationHook.ACCESS_EXPIRING_ROLE_OWNER: ("notifications.expiring_access.sent", {"kind": "role_owner"}),
    NotificationHook.ACCESS_ROLE_REQUEST_CREATED: ("notifications.role_request_created.sent", None),
    NotificationHook.ACCESS_ROLE_REQUEST_COMPLETED: ("notifications.role_request_completed.sent", None),
    NotificationHook.ACCESS_GROUP_REQUEST_CREATED: ("notifications.group_request_created.sent", None),
    NotificationHook.ACCESS_GROUP_REQUEST_COMPLETED: ("notifications.group_request_completed.sent", None),
}


async def _record_sent(metric_name: str, tags: Optional[Dict[str, str]] = None) -> None:
    try:
        coros = get_metrics_reporter_hook().record_counter(metric_name=metric_name, value=1, tags=tags)
    except Exception:
        logger.exception("Failed to record %s metric", metric_name)
        return
    await run_hooks_to_completion(coros, context=f"metrics record_counter {metric_name}")


class NotificationPluginSpec:
    @hookspec
    async def access_request_created(
        self, access_request: AccessRequest, group: OktaGroup, requester: OktaUser, approvers: list[OktaUser]
    ) -> None:
        """Notify the approvers of the access request."""

    @hookspec
    async def access_request_completed(
        self,
        access_request: AccessRequest,
        group: OktaGroup,
        requester: OktaUser,
        approvers: list[OktaUser],
        notify_requester: bool,
    ) -> None:
        """Notify the requester that their access request has been processed."""

    @hookspec
    async def access_expiring_user(
        self,
        user: OktaUser,
        expiration_datetime: datetime.datetime,
        okta_user_group_members: Optional[list[OktaUserGroupMember]],
    ) -> None:
        """Notify individuals that their access to a group is expiring soon"""

    @hookspec
    async def access_expiring_owner(
        self,
        owner: OktaUser,
        expiration_datetime: datetime.datetime,
        group_user_associations: Optional[list[OktaUserGroupMember]],
        role_group_associations: Optional[list[RoleGroupMap]],
    ) -> None:
        """Notify group owners that individuals or roles access to a group is expiring soon"""

    @hookspec
    async def access_expiring_role_owner(
        self,
        owner: OktaUser,
        roles: list[RoleGroupMap],
        expiration_datetime: datetime.datetime,
    ) -> None:
        """Notify role owners that roles they own will be losing access soon"""

    @hookspec
    async def access_role_request_created(
        self,
        role_request: RoleRequest,
        role: RoleGroup,
        group: OktaGroup,
        requester: OktaUser,
        approvers: list[OktaUser],
    ) -> None:
        """Notify the approvers of the role request."""

    @hookspec
    async def access_role_request_completed(
        self,
        role_request: RoleRequest,
        role: RoleGroup,
        group: OktaGroup,
        requester: OktaUser,
        approvers: list[OktaUser],
        notify_requester: bool,
    ) -> None:
        """Notify the requester that their role request has been processed."""

    @hookspec
    async def access_group_request_created(
        self,
        group_request: GroupRequest,
        requester: OktaUser,
        approvers: list[OktaUser],
    ) -> None:
        """Notify the approvers of the group request."""

    @hookspec
    async def access_group_request_completed(
        self,
        group_request: GroupRequest,
        group: Optional[OktaGroup],
        requester: OktaUser,
        approvers: list[OktaUser],
        notify_requester: bool,
    ) -> None:
        """Notify the requester that their group request has been processed."""


async def send_notification(hook: NotificationHook, /, **kwargs: Any) -> None:
    """Fire an async notification hook, swallow plugin errors, and record a
    "sent" counter when every implementation succeeded.

    This is the async replacement for the old ``@hookimpl(wrapper=True)``
    wrappers: pluggy cannot wrap coroutines, so exception handling and the
    success metric live here instead. Notification failures are logged and never
    propagate — a request must still succeed even if a plugin's DM/email fails,
    and approvers can be pinged manually from the UI.

    The plugin coroutines are run via ``run_hooks_to_completion`` (``asyncio.wait``,
    not ``gather``) so a cancelled request doesn't tear down an in-flight send and
    one plugin's failure doesn't cancel the others.

    ``kwargs`` are forwarded verbatim; pluggy passes each implementation only the
    parameters it declares, so extra kwargs (e.g. ``requester_role``) are ignored
    safely.
    """
    relay = get_notification_hook()
    _, exceptions = await run_hooks_to_completion(
        getattr(relay, hook)(**kwargs), context=f"{hook} notification callback"
    )
    if exceptions:
        # Failures are already logged; don't record a "sent" for a partial fire.
        return
    metric, tags = _SENT_METRICS[hook]
    await _record_sent(metric, tags)


def get_notification_hook() -> pluggy.HookRelay:
    global _cached_notification_hook

    if _cached_notification_hook is not None:
        return _cached_notification_hook

    pm = pluggy.PluginManager(notification_plugin_name)
    pm.add_hookspecs(NotificationPluginSpec)

    count = pm.load_setuptools_entrypoints(notification_plugin_name)
    print(f"Count of loaded notification plugins: {count}")
    verify_async_impls(pm, tuple(NotificationHook))
    _cached_notification_hook = pm.hook

    return _cached_notification_hook
