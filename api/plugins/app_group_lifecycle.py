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
    def get_plugin_config_properties(self) -> Optional[Dict[str, AppGroupLifecyclePluginConfigProperty]]:
        """Return the schema for configuration plugin data, a mapping of property IDs to descriptors."""

    @hookspec
    def validate_plugin_config(self, config: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Validate plugin config before saving. Returns a mapping of fields to error messages."""

    # Status hooks
    @hookspec
    def get_plugin_status_properties(self) -> Optional[Dict[str, AppGroupLifecyclePluginStatusProperty]]:
        """Return the schema for status plugin data, a mapping of property IDs to descriptors."""

    # Group lifecycle hooks
    @hookspec
    def group_created(self, session: Session, group: AppGroup) -> None:
        """Handle group creation."""

    @hookspec
    def group_deleted(self, session: Session, group: AppGroup) -> None:
        """Handle group deletion."""

    # Membership hooks
    @hookspec
    def group_members_added(self, session: Session, group: AppGroup, members: List[OktaUser]) -> None:
        """Handle member addition."""

    @hookspec
    def group_members_removed(self, session: Session, group: AppGroup, members: List[OktaUser]) -> None:
        """Handle member removal."""

    @hookspec
    def sync_all_group_membership(self, session: Session, app: App) -> None:
        """Bulk sync all groups for an app."""


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


def should_invoke_app_group_lifecycle_plugin(group: Any) -> bool:
    """Determine if an app group lifecycle plugin should be invoked for a given group."""
    return type(group) is AppGroup and group.app.app_group_lifecycle_plugin is not None