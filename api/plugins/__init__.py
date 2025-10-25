import pluggy

from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginStatusProperty,
    get_app_group_lifecycle_hook,
    get_app_group_lifecycle_plugin_by_id,
    get_app_group_lifecycle_plugins,
    should_invoke_app_group_lifecycle_plugin,
)
from api.plugins.conditional_access import ConditionalAccessResponse, get_conditional_access_hook
from api.plugins.notifications import get_notification_hook

condtional_access_hook_impl = pluggy.HookimplMarker("access_conditional_access")
notification_hook_impl = pluggy.HookimplMarker("access_notifications")
app_group_lifecycle_hook_impl = pluggy.HookimplMarker("access_app_group_lifecycle")

__all__ = [
    "AppGroupLifecyclePluginConfigProperty",
    "AppGroupLifecyclePluginStatusProperty",
    "app_group_lifecycle_hook_impl",
    "get_app_group_lifecycle_hook",
    "get_app_group_lifecycle_plugin_by_id",
    "get_app_group_lifecycle_plugins",
    "should_invoke_app_group_lifecycle_plugin",
    "ConditionalAccessResponse",
    "get_conditional_access_hook",
    "get_notification_hook",
    "notification_hook_impl",
]
