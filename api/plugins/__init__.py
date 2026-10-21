from importlib.metadata import entry_points

import pluggy

from api.log_filters import register_plugin_logger_prefixes

from api.plugins.app_group_lifecycle import (
    AppGroupLifecycleContext,
    AppGroupLifecycleHook,
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
    merge_app_lifecycle_plugin_data,
    validate_app_group_lifecycle_plugin_app_config,
    validate_app_group_lifecycle_plugin_group_config,
)
from api.plugins.conditional_access import (
    ConditionalAccessHook,
    conditional_access_plugin_name,
    ConditionalAccessResponse,
    evaluate_conditional_access,
    get_conditional_access_hook,
)
from api.plugins.metrics_reporter import get_metrics_reporter_hook, metrics_reporter_plugin_name
from api.plugins.notifications import (
    NotificationHook,
    get_notification_hook,
    notification_plugin_name,
    send_notification,
)

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
    _register_plugin_loggers()


def _register_plugin_loggers() -> None:
    """Attribute logs from every installed plugin package to that plugin."""
    groups = (
        app_group_lifecycle_plugin_name,
        conditional_access_plugin_name,
        notification_plugin_name,
        metrics_reporter_plugin_name,
        "access.commands",
    )
    register_plugin_logger_prefixes(ep.module.partition(".")[0] for group in groups for ep in entry_points(group=group))


__all__ = [
    # App Group Lifecycle Plugin
    "app_group_lifecycle_plugin_name",
    "AppGroupLifecycleContext",
    "AppGroupLifecycleHook",
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
    "merge_app_lifecycle_plugin_data",
    "validate_app_group_lifecycle_plugin_app_config",
    "validate_app_group_lifecycle_plugin_group_config",
    "app_group_lifecycle_hook_impl",
    # Conditional Access Plugin
    "ConditionalAccessHook",
    "ConditionalAccessResponse",
    "get_conditional_access_hook",
    "evaluate_conditional_access",
    "conditional_access_hook_impl",
    # Notifications Plugin
    "NotificationHook",
    "get_notification_hook",
    "send_notification",
    "notification_hook_impl",
    # Metrics Reporter Plugin
    "get_metrics_reporter_hook",
    # Eager loader
    "load_plugins",
]
