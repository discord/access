import pluggy

from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginFilteringError,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginSpec,
    AppGroupLifecyclePluginStatusProperty,
    app_group_lifecycle_plugin_name,
    get_app_group_lifecycle_hook,
    get_app_group_lifecycle_plugin_app_config_properties,
    get_app_group_lifecycle_plugin_app_status_properties,
    get_app_group_lifecycle_plugin_group_config_properties,
    get_app_group_lifecycle_plugin_group_status_properties,
    get_app_group_lifecycle_plugin_to_invoke,
    get_app_group_lifecycle_plugins,
    get_config_value,
    get_status_value,
    merge_app_lifecycle_plugin_data,
    set_status_value,
    validate_app_group_lifecycle_plugin_app_config,
    validate_app_group_lifecycle_plugin_group_config,
)
from api.plugins.conditional_access import ConditionalAccessResponse, get_conditional_access_hook
from api.plugins.notifications import get_notification_hook

app_group_lifecycle_hook_impl = pluggy.HookimplMarker("access_app_group_lifecycle")
conditional_access_hook_impl = pluggy.HookimplMarker("access_conditional_access")
notification_hook_impl = pluggy.HookimplMarker("access_notifications")

__all__ = [
    # App Group Lifecycle Plugin
    "app_group_lifecycle_plugin_name",
    "AppGroupLifecyclePluginConfigProperty",
    "AppGroupLifecyclePluginFilteringError",
    "AppGroupLifecyclePluginMetadata",
    "AppGroupLifecyclePluginSpec",
    "AppGroupLifecyclePluginStatusProperty",
    "get_app_group_lifecycle_hook",
    "get_app_group_lifecycle_plugins",
    "get_app_group_lifecycle_plugin_to_invoke",
    "get_app_group_lifecycle_plugin_app_config_properties",
    "get_app_group_lifecycle_plugin_group_config_properties",
    "get_app_group_lifecycle_plugin_app_status_properties",
    "get_app_group_lifecycle_plugin_group_status_properties",
    "get_config_value",
    "get_status_value",
    "merge_app_lifecycle_plugin_data",
    "set_status_value",
    "validate_app_group_lifecycle_plugin_app_config",
    "validate_app_group_lifecycle_plugin_group_config",
    "app_group_lifecycle_hook_impl",
    # Conditional Access Plugin
    "ConditionalAccessResponse",
    "get_conditional_access_hook",
    "conditional_access_hook_impl",
    # Notifications Plugin
    "get_notification_hook",
    "notification_hook_impl",
]
