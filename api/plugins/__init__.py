import pluggy

from api.plugins.audit_events import get_audit_events_hook
from api.plugins.conditional_access import ConditionalAccessResponse, get_conditional_access_hook
from api.plugins.notifications import get_notification_hook

condtional_access_hook_impl = pluggy.HookimplMarker("access_conditional_access")
notification_hook_impl = pluggy.HookimplMarker("access_notifications")
audit_events_hook_impl = pluggy.HookimplMarker("access_audit_events")

__all__ = [
    "ConditionalAccessResponse",
    "audit_events_hook_impl",
    "condtional_access_hook_impl",
    "get_audit_events_hook",
    "get_conditional_access_hook",
    "get_notification_hook",
    "notification_hook_impl",
]
