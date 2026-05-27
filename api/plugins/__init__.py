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
from api.plugins.metrics_reporter import get_metrics_reporter_hook
from api.plugins.notifications import get_notification_hook

app_group_lifecycle_hook_impl = pluggy.HookimplMarker("access_app_group_lifecycle")
conditional_access_hook_impl = pluggy.HookimplMarker("access_conditional_access")
notification_hook_impl = pluggy.HookimplMarker("access_notifications")


def load_plugins() -> None:
    """Eagerly trigger plugin discovery for every plugin type.

    Each `get_*_hook()` is memoized after the first call, so this is the
    one-shot entry point: subsequent calls (from hook invocations during
    a request, sync, notify, …) hit the cache. Surfaces any
    entry-point-load failure at startup instead of silently no-op'ing
    later when the hook fires."""
    get_app_group_lifecycle_hook()
    get_conditional_access_hook()
    get_notification_hook()
    get_metrics_reporter_hook()


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
    # Metrics Reporter Plugin
    "get_metrics_reporter_hook",
    # Eager loader
    "load_plugins",
]
