import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser

request_plugin_name = "access_requests"
hookspec = pluggy.HookspecMarker(request_plugin_name)
hookimpl = pluggy.HookimplMarker(request_plugin_name)

_cached_request_hook = None

logger = logging.getLogger(__name__)



@dataclass
class ConditionalAccessResponse:
    approved: bool
    reason: str = ''
    ending_at: Optional[datetime] = None

class RequestPluginSpec:
    @hookspec
    def access_request_created(self,
                               access_request: AccessRequest,
                               group: OktaGroup,
                               requester: OktaUser) -> Optional[ConditionalAccessResponse]:
        """Automatically approve, deny, or continue the access request."""


@hookimpl(wrapper=True)
def access_request_created(access_request: AccessRequest,
                           group: OktaGroup,
                           requester: OktaUser) -> Generator[None, None, Optional[ConditionalAccessResponse]]:
    try:
        # Trigger exception if it exists
        return (yield)
    except Exception:
        # Log and do not raise since request failures should not
        # break the flow. The access request can still be manually
        # approved or denied
        logger.error("Failed to execute request created callback")

    return None


def get_request_hook() -> pluggy.HookRelay:
    global _cached_request_hook

    if _cached_request_hook is not None:
        return _cached_request_hook

    pm = pluggy.PluginManager(request_plugin_name)
    pm.add_hookspecs(RequestPluginSpec)

    # Register the hook wrappers
    pm.register(sys.modules[__name__])

    count = pm.load_setuptools_entrypoints(request_plugin_name)
    print(f"Count of loaded request plugins: {count}")
    _cached_request_hook = pm.hook

    return _cached_request_hook
