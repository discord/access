import pluggy

from api.plugins.notifications import get_notification_hook

notification_hook_impl = pluggy.HookimplMarker("access_notifications")

__all__ = ["get_notification_hook", "notification_hook_impl"]
