import pluggy

from api.plugins.notifications import get_notification_hook
from api.plugins.requests import get_request_hook

notification_hook_impl = pluggy.HookimplMarker("access_notifications")
request_hook_impl = pluggy.HookimplMarker("access_requests")

__all__ = ["get_notification_hook", "notification_hook_impl", "get_request_hook", "request_hook_impl"]
