from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import (
    AppGroupLifecyclePluginAppConfigProperties,
    AppGroupLifecyclePluginAppStatusProperties,
    AppGroupLifecyclePluginGroupConfigProperties,
    AppGroupLifecyclePluginGroupStatusProperties,
    AppGroupLifecyclePluginList,
)

bp_name = "api-plugins"
bp_url_prefix = "/api/plugins"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(AppGroupLifecyclePluginList, "/app-group-lifecycle", endpoint="app_group_lifecycle_plugins")
api.add_resource(
    AppGroupLifecyclePluginAppConfigProperties,
    "/app-group-lifecycle/<string:plugin_id>/app-config-props",
    endpoint="app_group_lifecycle_plugin_app_config_props",
)
api.add_resource(
    AppGroupLifecyclePluginGroupConfigProperties,
    "/app-group-lifecycle/<string:plugin_id>/group-config-props",
    endpoint="app_group_lifecycle_plugin_group_config_props",
)
api.add_resource(
    AppGroupLifecyclePluginAppStatusProperties,
    "/app-group-lifecycle/<string:plugin_id>/app-status-props",
    endpoint="app_group_lifecycle_plugin_app_status_props",
)
api.add_resource(
    AppGroupLifecyclePluginGroupStatusProperties,
    "/app-group-lifecycle/<string:plugin_id>/group-status-props",
    endpoint="app_group_lifecycle_plugin_group_status_props",
)


def register_docs() -> None:
    docs.register(AppGroupLifecyclePluginList, blueprint=bp_name, endpoint="app_group_lifecycle_plugins")
    docs.register(
        AppGroupLifecyclePluginAppConfigProperties,
        blueprint=bp_name,
        endpoint="app_group_lifecycle_plugin_app_config_props",
    )
    docs.register(
        AppGroupLifecyclePluginGroupConfigProperties,
        blueprint=bp_name,
        endpoint="app_group_lifecycle_plugin_group_config_props",
    )
    docs.register(
        AppGroupLifecyclePluginAppStatusProperties,
        blueprint=bp_name,
        endpoint="app_group_lifecycle_plugin_app_status_props",
    )
    docs.register(
        AppGroupLifecyclePluginGroupStatusProperties,
        blueprint=bp_name,
        endpoint="app_group_lifecycle_plugin_group_status_props",
    )
