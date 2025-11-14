from dataclasses import asdict

from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource

from api.apispec import FlaskApiSpecDecorators
from api.plugins.app_group_lifecycle import (
    get_app_group_lifecycle_plugin_app_config_properties,
    get_app_group_lifecycle_plugin_app_status_properties,
    get_app_group_lifecycle_plugin_group_config_properties,
    get_app_group_lifecycle_plugin_group_status_properties,
    get_app_group_lifecycle_plugins,
)
from api.views.schemas import (
    AppGroupLifecyclePluginConfigPropertySchema,
    AppGroupLifecyclePluginMetadataSchema,
    AppGroupLifecyclePluginStatusPropertySchema,
)


class AppGroupLifecyclePluginList(MethodResource):
    """Resource for listing available app group lifecycle plugins."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginMetadataSchema)
    def get(self) -> ResponseReturnValue:
        """
        Get a list of all available app group lifecycle plugins.

        Returns:
            A list of plugin metadata objects.
        """
        plugins = get_app_group_lifecycle_plugins()
        # Sort by display name alphabetically (ascending)
        plugins = sorted(plugins, key=lambda p: p.display_name.lower())
        return [asdict(plugin) for plugin in plugins], 200


class AppGroupLifecyclePluginAppConfigProperties(MethodResource):
    """Resource for getting app-level configuration properties for a specific plugin."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginConfigPropertySchema)
    def get(self, plugin_id: str) -> ResponseReturnValue:
        """
        Get app-level configuration properties for a specific plugin.

        Args:
            plugin_id: The ID of the plugin

        Returns:
            Dictionary mapping property names to property schemas
        """
        # Verify the plugin is registered
        plugins = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
        if plugin_id not in plugins:
            return {"error": f"Plugin '{plugin_id}' not found"}, 404

        config_properties = get_app_group_lifecycle_plugin_app_config_properties(plugin_id)
        return {name: asdict(schema) for name, schema in config_properties.items()}, 200


class AppGroupLifecyclePluginGroupConfigProperties(MethodResource):
    """Resource for getting group-level configuration properties for a specific plugin."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginConfigPropertySchema)
    def get(self, plugin_id: str) -> ResponseReturnValue:
        """
        Get group-level configuration properties for a specific plugin.

        Args:
            plugin_id: The ID of the plugin

        Returns:
            Dictionary mapping property names to property schemas
        """
        # Verify the plugin is registered
        plugins = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
        if plugin_id not in plugins:
            return {"error": f"Plugin '{plugin_id}' not found"}, 404

        config_properties = get_app_group_lifecycle_plugin_group_config_properties(plugin_id)
        return {name: asdict(schema) for name, schema in config_properties.items()}, 200


class AppGroupLifecyclePluginAppStatusProperties(MethodResource):
    """Resource for getting app-level status properties for a specific plugin."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginStatusPropertySchema)
    def get(self, plugin_id: str) -> ResponseReturnValue:
        """
        Get app-level status properties for a specific plugin.

        Args:
            plugin_id: The ID of the plugin

        Returns:
            Dictionary mapping property names to property schemas
        """
        # Verify the plugin is registered
        plugins = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
        if plugin_id not in plugins:
            return {"error": f"Plugin '{plugin_id}' not found"}, 404

        status_properties = get_app_group_lifecycle_plugin_app_status_properties(plugin_id)
        return {name: asdict(schema) for name, schema in status_properties.items()}, 200


class AppGroupLifecyclePluginGroupStatusProperties(MethodResource):
    """Resource for getting group-level status properties for a specific plugin."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginStatusPropertySchema)
    def get(self, plugin_id: str) -> ResponseReturnValue:
        """
        Get group-level status properties for a specific plugin.

        Args:
            plugin_id: The ID of the plugin

        Returns:
            Dictionary mapping property names to property schemas
        """
        # Verify the plugin is registered
        plugins = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
        if plugin_id not in plugins:
            return {"error": f"Plugin '{plugin_id}' not found"}, 404

        status_properties = get_app_group_lifecycle_plugin_group_status_properties(plugin_id)
        return {name: asdict(schema) for name, schema in status_properties.items()}, 200
