import pluggy

from api.plugins.notifications import get_notification_hook
from api.plugins.requests import ConditionalAccessResponse, get_request_hook

notification_hook_impl = pluggy.HookimplMarker("access_notifications")
request_hook_impl = pluggy.HookimplMarker("access_requests")

__all__ = [
  "ConditionalAccessResponse",
  "get_notification_hook",
  "get_request_hook",
  "notification_hook_impl",
  "request_hook_impl",
]
