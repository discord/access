import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator, List, Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser, Tag

conditional_access_plugin_name = "access_conditional_access"
hookspec = pluggy.HookspecMarker(conditional_access_plugin_name)
hookimpl = pluggy.HookimplMarker(conditional_access_plugin_name)

_cached_conditional_access_hook = None

logger = logging.getLogger(__name__)


@dataclass
class ConditionalAccessResponse:
    approved: bool
    reason: str = ""
    ending_at: Optional[datetime] = None


class ConditionalAccessPluginSpec:
    @hookspec
    def access_request_created(
        self, access_request: AccessRequest, group: OktaGroup, group_tags: List[Tag], requester: OktaUser
    ) -> Optional[ConditionalAccessResponse]:
        """Automatically approve, deny, or continue the access request."""


@hookimpl(wrapper=True)
def access_request_created(
    access_request: AccessRequest, group: OktaGroup, group_tags: List[Tag], requester: OktaUser
) -> Generator[Any, None, Optional[ConditionalAccessResponse]] | List[Optional[ConditionalAccessResponse]]:
    try:
        # Trigger exception if it exists
        return (yield)
    except Exception:
        # Log and do not raise since request failures should not
        # break the flow. The access request can still be manually
        # approved or denied
        logger.exception("Failed to execute request created callback")

    return []


def get_conditional_access_hook() -> pluggy.HookRelay:
    global _cached_conditional_access_hook

    if _cached_conditional_access_hook is not None:
        return _cached_conditional_access_hook

    pm = pluggy.PluginManager(conditional_access_plugin_name)
    pm.add_hookspecs(ConditionalAccessPluginSpec)

    # Register the hook wrappers
    pm.register(sys.modules[__name__])

    count = pm.load_setuptools_entrypoints(conditional_access_plugin_name)
    print(f"Count of loaded conditional access plugins: {count}")
    _cached_conditional_access_hook = pm.hook

    return _cached_conditional_access_hook
