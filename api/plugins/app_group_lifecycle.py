import logging
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Set

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
    """Schema for an app group lifecycle plugin."""

    id: str
    display_name: str
    description: str


_cached_plugin_registry: List[AppGroupLifecyclePluginMetadata] | None = None

@dataclass
class AppGroupLifecyclePluginConfigProperty:
    """Schema for a configuration property required by an app group lifecycle plugin."""

    display_name: str
    help_text: Optional[str] = None
    type: Literal["text", "number", "boolean"] = "text"
    default_value: Any = None
    required: bool = False
    validation: Optional[Dict[str, Any]] = None


@dataclass
class AppGroupLifecyclePluginStatusProperty:
    """Schema for a status property exposed by an app group lifecycle plugin."""

    display_name: str
    help_text: Optional[str] = None
    type: Literal["text", "number", "date", "boolean"] = "text"


class AppGroupLifecyclePluginSpec:
    """Plugin specification for managing app group lifecycles."""

    @hookspec
    def get_plugin_metadata(self) -> Optional[AppGroupLifecyclePluginMetadata]:
        """Return the metadata for this plugin implementation."""

    # Configuration hooks
    @hookspec
    def get_plugin_config_properties(self, plugin_id: Optional[str] = None) -> Optional[Dict[str, AppGroupLifecyclePluginConfigProperty]]:
        """
        Return the schema for configuration plugin data, a mapping of property IDs to descriptors.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def validate_plugin_config(self, config: Dict[str, Any], plugin_id: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Validate plugin config before saving.

        Args:
            config: The configuration to validate.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.

        Returns:
            A dictionary mapping any invalid fields to error messages.
        """

    # Status hooks
    @hookspec
    def get_plugin_status_properties(self, plugin_id: Optional[str] = None) -> Optional[Dict[str, AppGroupLifecyclePluginStatusProperty]]:
        """
        Return the schema for status plugin data, a mapping of property IDs to descriptors.

        Args:
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    # Group lifecycle hooks
    @hookspec
    def group_created(self, session: Session, group: AppGroup, plugin_id: Optional[str] = None) -> None:
        """Handle group creation.

        Args:
            session: The Access database session.
            group: The app group that was created.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def group_deleted(self, session: Session, group: AppGroup, plugin_id: Optional[str] = None) -> None:
        """Handle group deletion.

        Args:
            session: The Access database session.
            group: The app group that was deleted.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    # Membership hooks
    @hookspec
    def group_members_added(self, session: Session, group: AppGroup, members: List[OktaUser], plugin_id: Optional[str] = None) -> None:
        """Handle member addition.

        Args:
            session: The Access database session.
            group: The app group to which members were added.
            members: The list of users that were added to the group.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def group_members_removed(self, session: Session, group: AppGroup, members: List[OktaUser], plugin_id: Optional[str] = None) -> None:
        """Handle member removal.

        Args:
            session: The Access database session.
            group: The app group from which members were removed.
            members: The list of users that were removed from the group.
            plugin_id: If provided, only the plugin matching this ID should respond.
                       If None, all plugins may respond.
        """

    @hookspec
    def sync_all_group_membership(self, session: Session, app: App, plugin_id: Optional[str] = None) -> None:
        """Bulk sync all groups for an app.

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


def get_app_group_lifecycle_plugins() -> List[AppGroupLifecyclePluginMetadata]:
    """
    Get a registry of all loaded app group lifecycle plugins with their metadata.
    Returns a dictionary mapping plugin IDs to plugin metadata.
    """
    global _cached_plugin_registry

    if _cached_plugin_registry is not None:
        return _cached_plugin_registry

    hook = get_app_group_lifecycle_hook()

    # Collect metadata from all registered plugins
    plugins: List[AppGroupLifecyclePluginMetadata] = [plugin for plugin in hook.get_plugin_metadata() if plugin is not None]

    # Validate uniqueness
    seen_ids: Set[str] = set()
    seen_display_names: Set[str] = set()
    seen_descriptions: Set[str] = set()

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


def get_app_group_lifecycle_plugin_to_invoke(group: Any) -> Optional[str]:
    """
    Determine the ID of the app group lifecycle plugin to invoke for a given group, if any.

    Args:
        group: The app group for which to determine the app group lifecycle plugin to invoke.
    """
    if type(group) is not AppGroup or group.app.app_group_lifecycle_plugin is None:
        return None

    return group.app.app_group_lifecycle_plugin

def get_app_group_lifecycle_plugin_data_key(plugin_id: str) -> str:
    """
    Get the key for storing data for a particular app group lifecycle plugin in the plugin_data property of an app group.

    Args:
        plugin_id: The ID of the app group lifecycle plugin.
    """
    return f"{app_group_lifecycle_plugin_name}___{plugin_id}"

def get_app_group_lifecycle_plugin_configuration_properties(plugin_id: str) -> Dict[str, Any]:
    """
    Get the configuration propertiesfor a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the app group lifecycle plugin.

    Returns:
        A dictionary mapping configuration property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    responses = [response for response in hook.get_plugin_config_properties(plugin_id=plugin_id) if response is not None]
    
    assert len(responses) == 1, f"Expected one response for plugin '{plugin_id}' but got {len(responses)}"
    return responses[0]

def validate_app_group_lifecycle_plugin_data(plugin_data:Any, plugin_id: str) -> Dict[str, str]:
    """
    Validate the data for a particular app group lifecycle plugin.

    Args:
        plugin_data: The data to validate.
        plugin_id: The ID of the app group lifecycle plugin.

    Returns:
        A dictionary mapping any invalid fields to error messages.
    """
    if not isinstance(plugin_data, dict):
        raise ValueError(
            f"The data for app group lifecycle plugin '{plugin_id}' must be a dictionary"
        )

    # Extract configuration from the nested structure
    configuration = plugin_data.get("configuration", {})
    if not isinstance(configuration, dict):
        raise ValueError(
            f"The configuration property in the data for app group lifecycle plugin '{plugin_id}' must be a dictionary"
        )

    hook = get_app_group_lifecycle_hook()
    responses = [response for response in hook.validate_plugin_config(config=configuration, plugin_id=plugin_id) if response is not None]

    assert len(responses) == 1, f"Expected one validation response for app group lifecycle plugin '{plugin_id}', but got {len(responses)}"

    return responses[0]

def get_app_group_lifecycle_plugin_status_properties(plugin_id: str) -> Dict[str, Any]:
    """
    Get the status properties for a particular app group lifecycle plugin.

    Args:
        plugin_id: The ID of the app group lifecycle plugin.

    Returns:
        A dictionary mapping status property names to schemas.
    """
    hook = get_app_group_lifecycle_hook()
    responses = [response for response in hook.get_plugin_status_properties(plugin_id=plugin_id) if response is not None]

    assert len(responses) == 1, f"Expected one response for plugin '{plugin_id}' but got {len(responses)}"

    return responses[0]