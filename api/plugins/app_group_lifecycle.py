import logging
import sys
from dataclasses import asdict, dataclass
from typing import Any, Literal

import pluggy
from sqlalchemy.orm import Session

from api.models import App, AppGroup, OktaUser

app_group_lifecycle_plugin_name = "access_app_group_lifecycle"
hookspec = pluggy.HookspecMarker(app_group_lifecycle_plugin_name)
hookimpl = pluggy.HookimplMarker(app_group_lifecycle_plugin_name)

_cached_app_group_lifecycle_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)


@dataclass
class AppGroupLifecyclePluginMetadata:
    """Metadata for an app group lifecycle plugin."""

    id: str
    display_name: str
    description: str


_cached_plugin_registry: list[AppGroupLifecyclePluginMetadata] | None = None


@dataclass
class AppGroupLifecyclePluginConfigProperty:
    """Schema for a configuration property required by an app group lifecycle plugin."""

    display_name: str
    help_text: str | None = None
    type: Literal["text", "number", "boolean"] = "text"
    default_value: Any = None
    required: bool = False
    validation: dict[str, Any] | None = None


@dataclass
class AppGroupLifecyclePluginStatusProperty:
    """Schema for a status property exposed by an app group lifecycle plugin."""

    display_name: str
    help_text: str | None = None
    type: Literal["text", "number", "date", "boolean"] = "text"


@dataclass
class AppGroupLifecyclePluginData:
    """Data for an app group lifecycle plugin."""

    configuration: dict[str, Any]
    status: dict[str, Any]


class AppGroupLifecyclePluginSpec:
    """Plugin specification for managing app group lifecycles."""

    @hookspec
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        """Return the metadata for this plugin implementation."""

    # Configuration hooks

    @hookspec
    def get_plugin_app_config_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """
        Return the schema for app configuration plugin data, a mapping of property IDs to descriptors.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def validate_plugin_app_config(self, config: dict[str, Any], plugin_id: str | None) -> dict[str, str] | None:
        """
        Validate app plugin config before saving.

        Args:
            config: The configuration to validate.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A dictionary mapping any invalid fields to error messages, or an empty dictionary if the configuration is valid.
        """

    @hookspec
    def get_plugin_group_config_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """
        Return the schema for app group configuration plugin data, a mapping of property IDs to descriptors.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def validate_plugin_group_config(
        self, config: dict[str, Any], plugin_id: str | None
    ) -> dict[str, str] | None:
        """
        Validate app group plugin config before saving.

        Args:
            config: The configuration to validate.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A dictionary mapping any invalid fields to error messages.
        """

    # Status hooks

    @hookspec
    def get_plugin_app_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """
        Return the schema for app-level status plugin data, a mapping of property IDs to descriptors.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def get_plugin_group_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """
        Return the schema for group-level status plugin data, a mapping of property IDs to descriptors.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    # Group lifecycle hooks

    @hookspec
    def group_created(self, session: Session, group: AppGroup, plugin_id: str | None) -> None:
        """
        Handle group creation.

        Args:
            session: The Access database session.
            group: The app group that was created.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def group_deleted(self, session: Session, group: AppGroup, plugin_id: str | None) -> None:
        """
        Handle group deletion.

        Args:
            session: The Access database session.
            group: The app group that was deleted.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    # Membership hooks

    @hookspec
    def group_members_added(
        self, session: Session, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        """
        Handle member addition.

        Args:
            session: The Access database session.
            group: The app group to which members were added.
            members: The list of users that were added to the group.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def group_members_removed(
        self, session: Session, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        """
        Handle member removal.

        Args:
            session: The Access database session.
            group: The app group from which members were removed.
            members: The list of users that were removed from the group.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def sync_all_group_membership(self, session: Session, app: App, plugin_id: str | None) -> None:
        """
        Bulk sync all group memberships for an app. This is invoked periodically by a CLI command `flask sync-app-group-memberships`.

        Args:
            session: The Access database session.
            app: The app for which to sync all group membership.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """


def get_app_group_lifecycle_hook() -> pluggy.HookRelay:
    """Get the hook relay for app group lifecycle plugins."""
    global _cached_app_group_lifecycle_hook

    if _cached_app_group_lifecycle_hook is not None:
        return _cached_app_group_lifecycle_hook

    pm = pluggy.PluginManager(app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)

    # Register the hook wrappers
    pm.register(sys.modules[__name__])

    count = pm.load_setuptools_entrypoints(app_group_lifecycle_plugin_name)
    logger.info(f"Loaded {count} app group lifecycle plugin(s)")

    _cached_app_group_lifecycle_hook = pm.hook

    return _cached_app_group_lifecycle_hook


def get_app_group_lifecycle_plugins() -> list[AppGroupLifecyclePluginMetadata]:
    """
    Get a registry of all loaded app group lifecycle plugins with their metadata.

    Returns:
        A list of plugin metadata objects.
    """
    global _cached_plugin_registry

    if _cached_plugin_registry is not None:
        return _cached_plugin_registry

    hook = get_app_group_lifecycle_hook()

    # Collect metadata from all registered plugins
    plugins: list[AppGroupLifecyclePluginMetadata] = [
        plugin for plugin in hook.get_plugin_metadata() if plugin is not None
    ]

    # Validate uniqueness
    seen_ids: set[str] = set()
    seen_display_names: set[str] = set()
    seen_descriptions: set[str] = set()

    for plugin in plugins:
        if not plugin.id:
            raise ValueError("Plugin ID is required")
        if not plugin.display_name:
            raise ValueError(f"Display name is required but missing for plugin {plugin.id}")
        if not plugin.description:
            raise ValueError(f"Description is required but missing for plugin {plugin.id}")

        if plugin.id in seen_ids:
            raise ValueError(f"Duplicate plugin ID detected: {plugin.id}")
        if plugin.display_name in seen_display_names:
            raise ValueError(f"Duplicate plugin display name detected: {plugin.display_name}")
        if plugin.description in seen_descriptions:
            raise ValueError(f"Duplicate plugin description detected: {plugin.description}")

        seen_ids.add(plugin.id)
        seen_display_names.add(plugin.display_name)
        seen_descriptions.add(plugin.description)

    _cached_plugin_registry = plugins
    logger.info(f"Registered {len(plugins)} app group lifecycle plugin(s): {[plugin.id for plugin in plugins]}")

    return _cached_plugin_registry


def get_app_group_lifecycle_plugin_to_invoke(group: Any) -> str | None:
    """
    Determine the ID of the app group lifecycle plugin to invoke for a given group, if any.

    Args:
        group: The app group for which to determine the app group lifecycle plugin to invoke.

    Returns:
        The ID of the app group lifecycle plugin to invoke, or None if no plugin is configured.
    """
    if type(group) is not AppGroup or group.app.app_group_lifecycle_plugin is None:
        return None

    return group.app.app_group_lifecycle_plugin


def _get_data_for_plugin(plugin_data: dict[str, Any], plugin_id: str) -> AppGroupLifecyclePluginData:
    """
    Get the data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The app or group's raw plugin_data property.
        plugin_id: The ID of the plugin which should respond.

    Returns:
        The data for the plugin.
    """
    this_plugin_data = plugin_data.get(plugin_id, {})
    if not isinstance(this_plugin_data, dict):
        raise ValueError(f"The data for app group lifecycle plugin '{plugin_id}' must be a dictionary")

    configuration = this_plugin_data.get("configuration", {})
    if not isinstance(configuration, dict):
        raise ValueError(
            f"The configuration property in the data for app group lifecycle plugin '{plugin_id}' must be a dictionary"
        )

    status = this_plugin_data.get("status", {})
    if not isinstance(status, dict):
        raise ValueError(
            f"The status property in the data for app group lifecycle plugin '{plugin_id}' must be a dictionary"
        )

    return AppGroupLifecyclePluginData(configuration, status)


def get_config_value(
    app_or_group: App | AppGroup, config_property_name: str, plugin_id: str, default: Any | None = None
) -> Any:
    """
    Get a configuration value for a particular app group lifecycle plugin.
    Should only be called by the plugin itself.

    Args:
        app_or_group: The app or group to get the configuration value for.
        config_property_name: The name of the configuration property to get.
        plugin_id: The ID of the plugin.
        default: The default value to return if the property is not found.

    Returns:
        The configuration value for the property, or the default value if the property is not found.
    """
    return _get_data_for_plugin(app_or_group.plugin_data, plugin_id).configuration.get(config_property_name, default)


def get_status_value(
    app_or_group: App | AppGroup, status_property_name: str, plugin_id: str, default: Any | None = None
) -> Any:
    """
    Get a status value for a particular app group lifecycle plugin.
    Should only be called by the plugin itself.

    Args:
        app_or_group: The app or group to get the status value for.
        status_property_name: The name of the status property to get.
        plugin_id: The ID of the plugin.
        default: The default value to return if the property is not found.

    Returns:
        The status value for the property, or the default value if the property is not found.
    """
    return _get_data_for_plugin(app_or_group.plugin_data, plugin_id).status.get(status_property_name, default)


def set_status_value(app_or_group: App | AppGroup, status_property_name: str, value: Any, plugin_id: str) -> None:
    """
    set a status value for a particular app group lifecycle plugin.
    Should only be called by the plugin itself.

    Args:
        app_or_group: The app or group to set the status value for.
        status_property_name: The name of the status property to set.
        value: The value to set.
        plugin_id: The ID of the plugin.
    """
    data = _get_data_for_plugin(app_or_group.plugin_data, plugin_id)
    data.status[status_property_name] = value
    app_or_group.plugin_data[plugin_id] = asdict(data)


def merge_app_lifecycle_plugin_data(app_or_group: App | AppGroup, old_plugin_data: dict[str, Any]) -> None:
    """
    Update the app lifecycle plugin data on the new app or group object by merging with the plugin data from the existing object.

    Args:
        app_or_group: The existing app or group for which to update the plugin data.
        old_plugin_data: The plugin data of the existing app or group object, which may be a partial patch.
    """
    app_group_lifecycle_plugin_ids = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
    for plugin_id in old_plugin_data:
        if plugin_id in app_group_lifecycle_plugin_ids:
            data = _get_data_for_plugin(old_plugin_data, plugin_id)
            patch_data = _get_data_for_plugin(app_or_group.plugin_data, plugin_id)
            data.configuration.update(patch_data.configuration)
            data.status.update(patch_data.status)
            app_or_group.plugin_data[plugin_id] = asdict(obj=data)


class AppGroupLifecyclePluginFilteringError(Exception):
    """Exception raised when no or multiple app group lifecycle plugins respond to a hook call."""

    def __init__(self, plugin_id: str, response_count: int):
        self.plugin_id = plugin_id
        self.response_count = response_count
        super().__init__(f"Expected one response for plugin '{plugin_id}' but got {response_count}")


def _get_hook_call_response(hook_caller: pluggy.HookCaller, plugin_id: str, **args: dict[str, Any]) -> Any:
    """
    Get a response from a particular app group lifecycle plugin.

    Args:
        hook_caller: The hook caller to use.
        plugin_id: The ID of the plugin which should respond.
        **args: Additional arguments to pass to the hook caller.

    Returns:
        The singular plugin response.
    """
    responses = [response for response in hook_caller(plugin_id=plugin_id, **args) if response is not None]
    if len(responses) != 1:
        raise AppGroupLifecyclePluginFilteringError(plugin_id, len(responses))
    return responses[0]


def get_app_group_lifecycle_plugin_app_config_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginConfigProperty]:
    """
    Get the app-level configuration properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping configuration property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_app_config_properties, plugin_id)


def get_app_group_lifecycle_plugin_group_config_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginConfigProperty]:
    """
    Get the group-level configuration properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping configuration property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_group_config_properties, plugin_id)


def validate_app_group_lifecycle_plugin_app_config(plugin_data: dict[str, Any], plugin_id: str) -> dict[str, str]:
    """
    Validate the app-level configuration data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The plugin data to validate.
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping any invalid fields to error messages.
    """
    configuration = _get_data_for_plugin(plugin_data, plugin_id).configuration
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.validate_plugin_app_config, plugin_id, config=configuration)


def validate_app_group_lifecycle_plugin_group_config(plugin_data: dict[str, Any], plugin_id: str) -> dict[str, str]:
    """
    Validate the group-level configuration data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The group's plugin data property.
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping any invalid fields to error messages.
    """
    configuration = _get_data_for_plugin(plugin_data, plugin_id).configuration
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.validate_plugin_group_config, plugin_id, config=configuration)


def get_app_group_lifecycle_plugin_app_status_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginStatusProperty]:
    """
    Get the app-level status properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping status property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_app_status_properties, plugin_id)


def get_app_group_lifecycle_plugin_group_status_properties(
    plugin_id: str,
) -> dict[str, AppGroupLifecyclePluginStatusProperty]:
    """
    Get the group-level status properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the plugin which should respond.

    Returns:
        A dictionary mapping status property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    return _get_hook_call_response(hook.get_plugin_group_status_properties, plugin_id)
