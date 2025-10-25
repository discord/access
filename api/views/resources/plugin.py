from dataclasses import asdict

from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource

from api.apispec import FlaskApiSpecDecorators
from api.plugins.app_group_lifecycle import (
    get_app_group_lifecycle_plugin_configuration_properties,
    get_app_group_lifecycle_plugin_status_properties,
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
        """Get a list of all available app group lifecycle plugins.

        Returns a list of plugin metadata objects.
        """
        plugins = get_app_group_lifecycle_plugins()
        # Sort by display name alphabetically (ascending)
        plugins = sorted(plugins, key=lambda p: p.display_name.lower())
        # Convert dataclass instances to dicts for serialization
        plugins_dicts = [asdict(plugin) for plugin in plugins]
        return plugins_dicts, 200


class AppGroupLifecyclePluginConfigProperties(MethodResource):
    """Resource for getting configuration properties for a specific plugin."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginConfigPropertySchema)
    def get(self, plugin_id: str) -> ResponseReturnValue:
        """Get configuration properties for a specific plugin.

        Args:
            plugin_id: The ID of the plugin

        Returns:
            Dictionary mapping property names to property schemas
        """
        # Verify the plugin is registered
        plugins = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
        if plugin_id not in plugins:
            return {"error": f"Plugin '{plugin_id}' not found"}, 404

        config_properties_raw = get_app_group_lifecycle_plugin_configuration_properties(plugin_id)
        config_properties = {}
        for name, schema in config_properties_raw.items():
            config_properties[name] = asdict(schema)

        return config_properties, 200


class AppGroupLifecyclePluginStatusProperties(MethodResource):
    """Resource for getting status properties for a specific plugin."""

    @FlaskApiSpecDecorators.response_schema(AppGroupLifecyclePluginStatusPropertySchema)
    def get(self, plugin_id: str) -> ResponseReturnValue:
        """Get status properties for a specific plugin.

        Args:
            plugin_id: The ID of the plugin

        Returns:
            Dictionary mapping property names to property schemas
        """
        # Verify the plugin is registered
        plugins = [plugin.id for plugin in get_app_group_lifecycle_plugins()]
        if plugin_id not in plugins:
            return {"error": f"Plugin '{plugin_id}' not found"}, 404

        status_properties_raw = get_app_group_lifecycle_plugin_status_properties(plugin_id)
        status_properties = {}
        for name, schema in status_properties_raw.items():
            status_properties[name] = asdict(schema)

        return status_properties, 200
