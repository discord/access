import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import pluggy

from api.models import (
    AccessRequest,
    App,
    GroupRequest,
    OktaGroup,
    OktaUser,
    RoleGroup,
    RoleRequest,
    ServicePrincipal,
    Tag,
)
from api.plugins._async_dispatch import run_hooks_to_completion, verify_async_impls

conditional_access_plugin_name = "access_conditional_access"
hookspec = pluggy.HookspecMarker(conditional_access_plugin_name)
hookimpl = pluggy.HookimplMarker(conditional_access_plugin_name)

_cached_conditional_access_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)


class ConditionalAccessHook(StrEnum):
    """Conditional-access hook names (StrEnum value == the pluggy hook name)."""

    ACCESS_REQUEST_CREATED = "access_request_created"
    ROLE_REQUEST_CREATED = "role_request_created"
    GROUP_REQUEST_CREATED = "group_request_created"


@dataclass
class ConditionalAccessResponse:
    """A plugin's decision on a request.

    Attributes:
        approved: True approves the request; False rejects it.
        reason: Recorded as the resolution reason.
        ending_at: Caps the access the approval grants. For a group request,
            it caps the owner grants.
        membership_ending_at: For a group request, caps the member grants.
            Ignored for other request types.
        allow_named_grants: For a group request, confirms the approval covers the
            owners and members the request names. An approval of a request that
            names anything beyond its requester as sole owner is ignored (the
            request stays pending) unless this is set. Ignored for other request types.
    """

    approved: bool
    reason: str = ""
    ending_at: datetime | None = None
    membership_ending_at: datetime | None = None
    allow_named_grants: bool = False


class ConditionalAccessPluginSpec:
    @hookspec
    async def access_request_created(
        self, access_request: AccessRequest, group: OktaGroup, group_tags: list[Tag], requester: OktaUser
    ) -> ConditionalAccessResponse | None:
        """Automatically approve, deny, or continue the access request."""

    @hookspec
    async def role_request_created(
        self, role_request: RoleRequest, role: RoleGroup, group: OktaGroup, group_tags: list[Tag], requester: OktaUser
    ) -> ConditionalAccessResponse | None:
        """Automatically approve, deny, or continue the role request."""

    @hookspec
    async def group_request_created(
        self,
        group_request: GroupRequest,
        requester: OktaUser | ServicePrincipal,
        app: App | None = None,
    ) -> ConditionalAccessResponse | None:
        """Automatically approve, deny, or continue the group request.

        ``requester`` is the user or service principal that filed the request. A
        rule that reads user-only attributes (such as ``email``) must check the
        type first; one keyed only on the app or group name applies to service
        principals' requests too.

        An approval creates the requested group with the owners and members the
        request names (users and roles; a user's request that names no owners
        names its requester). A service principal is never made an owner. An
        approval of a request that names anything beyond its requester as the
        sole owner (which every service principal's request does) takes effect
        only when the response sets ``allow_named_grants``; otherwise it is
        ignored and the request stays pending. The approval is recorded with no
        resolver. When the group can't be
        created as requested (for example, its name is already taken), the
        request stays pending and goes to its reviewers. The response's
        ``ending_at`` caps the owner grants and ``membership_ending_at`` caps the
        member grants: each can shorten the end date the request asked for but
        never extend it, and the new group's tag limits still apply. An end date
        that has already passed leaves the request pending for its reviewers.
        """


async def evaluate_conditional_access(
    hook: ConditionalAccessHook, /, **kwargs: Any
) -> list[ConditionalAccessResponse | None]:
    """Run an async conditional-access hook and return each plugin's response.

    This is the async replacement for the old ``@hookimpl(wrapper=True)``
    wrappers: pluggy cannot wrap coroutines, so the run + error handling lives
    here. Implementations run via ``run_hooks_to_completion`` (``asyncio.wait``,
    not ``gather``) so one plugin's failure doesn't cancel the others; a failing
    plugin is logged and simply omitted from the responses, leaving that decision
    to manual approval/denial rather than breaking the request.

    ``kwargs`` are forwarded verbatim; pluggy passes each implementation only the
    parameters it declares, so extra kwargs (e.g. ``requester_role``) are ignored
    safely.
    """
    relay = get_conditional_access_hook()
    results, _ = await run_hooks_to_completion(
        getattr(relay, hook)(**kwargs), context=f"{hook} conditional access callback"
    )
    return list(results)


def get_conditional_access_hook() -> pluggy.HookRelay:
    global _cached_conditional_access_hook

    if _cached_conditional_access_hook is not None:
        return _cached_conditional_access_hook

    pm = pluggy.PluginManager(conditional_access_plugin_name)
    pm.add_hookspecs(ConditionalAccessPluginSpec)

    count = pm.load_setuptools_entrypoints(conditional_access_plugin_name)
    print(f"Count of loaded conditional access plugins: {count}")
    verify_async_impls(pm, tuple(ConditionalAccessHook))
    _cached_conditional_access_hook = pm.hook

    return _cached_conditional_access_hook
