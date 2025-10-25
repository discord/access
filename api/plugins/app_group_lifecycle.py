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
_cached_plugin_registry: Dict[str, Dict[str, str]] | None = None

logger = logging.getLogger(__name__)


@dataclass
class AppGroupLifecyclePluginStatusProperty:
    """Schema for a status property exposed by an app group lifecycle plugin."""

    display_name: str
    help_text: Optional[str] = None
    type: Literal["text", "number", "date", "boolean"] = "text"


@dataclass
class AppGroupLifecyclePluginConfigProperty:
    """Schema for a configuration property required by an app group lifecycle plugin."""

    display_name: str
    help_text: Optional[str] = None
    type: Literal["text", "number", "boolean"] = "text"
    default_value: Any = None
    required: bool = False
    validation: Optional[Dict[str, Any]] = None


class AppGroupLifecyclePluginSpec:
    """Plugin specification for managing app group lifecycles."""

    # Configuration hooks
    @hookspec
    def get_plugin_id(self) -> Optional[str]:
        """Return a unique plugin ID."""

    @hookspec
    def get_plugin_display_name(self) -> Optional[str]:
        """Return a human-readable name."""

    @hookspec
    def get_plugin_description(self) -> Optional[str]:
        """Return a description of the plugin's functionality."""

    @hookspec
    def get_plugin_config_properties(self) -> Optional[Dict[str, AppGroupLifecyclePluginConfigProperty]]:
        """Return the schema for configuration plugin data, a mapping of property IDs to descriptors."""

    @hookspec
    def validate_plugin_config(
        self, session: Session, app: App, group: AppGroup, config: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
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


def get_app_group_lifecycle_plugins() -> Dict[str, Dict[str, str]]:
    """
    Get a registry of all loaded app group lifecycle plugins with their metadata.
    Returns a dictionary mapping plugin IDs to plugin metadata.
    """
    global _cached_plugin_registry

    if _cached_plugin_registry is not None:
        return _cached_plugin_registry

    hook = get_app_group_lifecycle_hook()
    registry = {}

    # Collect metadata from all registered plugins
    plugin_ids: List[Optional[str]] = hook.get_plugin_id()
    display_names: List[Optional[str]] = hook.get_plugin_display_name()
    descriptions: List[Optional[str]] = hook.get_plugin_description()

    # Validate uniqueness
    seen_ids: Set[str] = set()
    seen_display_names: Set[str] = set()
    seen_descriptions: Set[str] = set()

    for plugin_id, display_name, description in zip(plugin_ids, display_names, descriptions):
        if not plugin_id:
            raise ValueError("Plugin ID is required")
        if not display_name:
            raise ValueError(f"Display name is required but missing for plugin {plugin_id}")
        if not description:
            raise ValueError(f"Description is required but missing for plugin {plugin_id}")
        if plugin_id in seen_ids:
            raise ValueError(f"Duplicate plugin ID detected: {plugin_id}")
        if display_name in seen_display_names:
            raise ValueError(f"Duplicate plugin display name detected: {display_name}")
        if description in seen_descriptions:
            raise ValueError(f"Duplicate plugin description detected: {description}")

        seen_ids.add(plugin_id)
        seen_display_names.add(display_name)
        seen_descriptions.add(description)

        registry[plugin_id] = {
            "display_name": display_name,
            "description": description,
        }

    _cached_plugin_registry = registry
    logger.info(f"Registered {len(registry)} app group lifecycle plugin(s): {list(registry.keys())}")

    return _cached_plugin_registry


def get_app_group_lifecycle_plugin_by_id(plugin_id: str) -> Optional[Dict[str, str]]:
    """Get app group lifecycle plugin metadata by ID."""
    registry = get_app_group_lifecycle_plugins()
    return registry.get(plugin_id)


def should_invoke_app_group_lifecycle_plugin(group: Any) -> bool:
    """Determine if an app group lifecycle plugin should be invoked for a given group."""
    return type(group) is AppGroup and group.app.app_group_lifecycle_plugin is not None