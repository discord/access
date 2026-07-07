import asyncio
import datetime
import logging
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
from api.plugins._async_dispatch import verify_async_impls
from api.plugins.metrics_reporter import get_metrics_reporter_hook

notification_plugin_name = "access_notifications"
hookspec = pluggy.HookspecMarker(notification_plugin_name)
hookimpl = pluggy.HookimplMarker(notification_plugin_name)

_cached_notification_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)

# hook name -> (metric name, static tags) recorded once the hook fans out
# successfully. Kept here (not at call sites) so the "sent" accounting lives
# next to the spec it measures.
_SENT_METRICS: dict[str, tuple[str, Optional[Dict[str, str]]]] = {
    "access_request_created": ("notifications.access_request_created.sent", None),
    "access_request_completed": ("notifications.access_request_completed.sent", None),
    "access_expiring_user": ("notifications.expiring_access.sent", {"kind": "user"}),
    "access_expiring_owner": ("notifications.expiring_access.sent", {"kind": "owner"}),
    "access_expiring_role_owner": ("notifications.expiring_access.sent", {"kind": "role_owner"}),
    "access_role_request_created": ("notifications.role_request_created.sent", None),
    "access_role_request_completed": ("notifications.role_request_completed.sent", None),
    "access_group_request_created": ("notifications.group_request_created.sent", None),
    "access_group_request_completed": ("notifications.group_request_completed.sent", None),
}


async def _record_sent(metric_name: str, tags: Optional[Dict[str, str]] = None) -> None:
    try:
        await asyncio.gather(*get_metrics_reporter_hook().record_counter(metric_name=metric_name, value=1, tags=tags))
    except Exception:
        logger.exception("Failed to record %s metric", metric_name)


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


async def send_notification(hook_name: str, /, **kwargs: Any) -> None:
    """Fire an async notification hook, swallow plugin errors, and record a
    "sent" counter on success.

    This is the async replacement for the old ``@hookimpl(wrapper=True)``
    wrappers: pluggy cannot wrap coroutines, so exception handling and the
    success metric live here instead. Notification failures are logged and never
    propagate — a request must still succeed even if a plugin's DM/email fails,
    and approvers can be pinged manually from the UI.

    ``kwargs`` are forwarded verbatim; pluggy passes each implementation only the
    parameters it declares, so extra kwargs (e.g. ``requester_role``) are ignored
    safely.
    """
    hook = get_notification_hook()
    try:
        await asyncio.gather(*getattr(hook, hook_name)(**kwargs))
    except Exception:
        logger.exception("Failed to execute %s notification callback", hook_name)
        return
    metric, tags = _SENT_METRICS[hook_name]
    await _record_sent(metric, tags)


def get_notification_hook() -> pluggy.HookRelay:
    global _cached_notification_hook

    if _cached_notification_hook is not None:
        return _cached_notification_hook

    pm = pluggy.PluginManager(notification_plugin_name)
    pm.add_hookspecs(NotificationPluginSpec)

    count = pm.load_setuptools_entrypoints(notification_plugin_name)
    print(f"Count of loaded notification plugins: {count}")
    verify_async_impls(pm, tuple(_SENT_METRICS.keys()))
    _cached_notification_hook = pm.hook

    return _cached_notification_hook
