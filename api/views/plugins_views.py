from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import (
    AppGroupLifecyclePluginConfigProperties,
    AppGroupLifecyclePluginList,
    AppGroupLifecyclePluginStatusProperties,
)

bp_name = "api-plugins"
bp_url_prefix = "/api/plugins"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(
    AppGroupLifecyclePluginList, "/app-group-lifecycle", endpoint="app_group_lifecycle_plugins"
)
api.add_resource(
    AppGroupLifecyclePluginConfigProperties,
    "/app-group-lifecycle/<string:plugin_id>/config",
    endpoint="app_group_lifecycle_plugin_config",
)
api.add_resource(
    AppGroupLifecyclePluginStatusProperties,
    "/app-group-lifecycle/<string:plugin_id>/status",
    endpoint="app_group_lifecycle_plugin_status",
)


def register_docs() -> None:
    docs.register(AppGroupLifecyclePluginList, blueprint=bp_name, endpoint="app_group_lifecycle_plugins")
    docs.register(
        AppGroupLifecyclePluginConfigProperties, blueprint=bp_name, endpoint="app_group_lifecycle_plugin_config"
    )
    docs.register(
        AppGroupLifecyclePluginStatusProperties, blueprint=bp_name, endpoint="app_group_lifecycle_plugin_status"
    )
