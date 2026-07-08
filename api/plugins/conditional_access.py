import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

import pluggy

from api.models import AccessRequest, App, GroupRequest, OktaGroup, OktaUser, RoleGroup, RoleRequest, Tag
from api.plugins._async_dispatch import run_hooks_to_completion, verify_async_impls

conditional_access_plugin_name = "access_conditional_access"
hookspec = pluggy.HookspecMarker(conditional_access_plugin_name)
hookimpl = pluggy.HookimplMarker(conditional_access_plugin_name)

_cached_conditional_access_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)

_HOOK_NAMES = ("access_request_created", "role_request_created", "group_request_created")


@dataclass
class ConditionalAccessResponse:
    approved: bool
    reason: str = ""
    ending_at: Optional[datetime] = None


class ConditionalAccessPluginSpec:
    @hookspec
    async def access_request_created(
        self, access_request: AccessRequest, group: OktaGroup, group_tags: List[Tag], requester: OktaUser
    ) -> Optional[ConditionalAccessResponse]:
        """Automatically approve, deny, or continue the access request."""

    @hookspec
    async def role_request_created(
        self, role_request: RoleRequest, role: RoleGroup, group: OktaGroup, group_tags: List[Tag], requester: OktaUser
    ) -> Optional[ConditionalAccessResponse]:
        """Automatically approve, deny, or continue the role request."""

    @hookspec
    async def group_request_created(
        self,
        group_request: GroupRequest,
        requester: OktaUser,
        app: Optional[App] = None,
    ) -> Optional[ConditionalAccessResponse]:
        """Automatically approve, deny, or continue the group request."""


async def evaluate_conditional_access(hook_name: str, /, **kwargs: Any) -> list[Optional[ConditionalAccessResponse]]:
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
    hook = get_conditional_access_hook()
    results, _ = await run_hooks_to_completion(
        getattr(hook, hook_name)(**kwargs), context=f"{hook_name} conditional access callback"
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
    verify_async_impls(pm, _HOOK_NAMES)
    _cached_conditional_access_hook = pm.hook

    return _cached_conditional_access_hook
