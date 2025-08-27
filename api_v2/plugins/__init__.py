import pluggy

from api.plugins.conditional_access import ConditionalAccessResponse, get_conditional_access_hook
from api.plugins.notifications import get_notification_hook

condtional_access_hook_impl = pluggy.HookimplMarker("access_conditional_access")
notification_hook_impl = pluggy.HookimplMarker("access_notifications")

__all__ = [
    "ConditionalAccessResponse",
    "conditional_access_hook_impl",
    "get_conditional_access_hook",
    "get_notification_hook",
    "notification_hook_impl",
]
