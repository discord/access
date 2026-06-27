"""Plugins router. Read-only metadata about app group lifecycle plugins."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from sqlalchemy import select

from api.auth.dependencies import CurrentUserId
from api.database import DbSession
from api.models import App
from api.plugins.app_group_lifecycle import (
    PluginNotFoundError,
    get_app_group_lifecycle_plugin_app_config_properties,
    get_app_group_lifecycle_plugin_app_status_properties,
    get_app_group_lifecycle_plugin_group_config_properties,
    get_app_group_lifecycle_plugin_group_status_properties,
    get_app_group_lifecycle_plugins,
)
from api.schemas import (
    AppGroupLifecyclePluginAppConfig,
    AppGroupLifecyclePluginAppStatus,
    AppGroupLifecyclePluginGroupConfig,
    AppGroupLifecyclePluginGroupStatus,
    AppGroupLifecyclePlugins,
)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _ensure_plugin(plugin_id: str) -> None:
    # Raises `PluginNotFoundError`, which the exception handler at
    # `api/exception_handlers.py:plugin_not_found_handler` serializes as
    # `{"error": "Plugin '...' not found"}` with status 404.
    plugins = [p.id for p in get_app_group_lifecycle_plugins()]
    if plugin_id not in plugins:
        raise PluginNotFoundError(plugin_id)


@router.get("/app-group-lifecycle", name="app_group_lifecycle_plugins")
def list_plugins(current_user_id: CurrentUserId) -> AppGroupLifecyclePlugins:
    plugins = sorted(get_app_group_lifecycle_plugins(), key=lambda p: p.display_name.lower())
    return AppGroupLifecyclePlugins.model_validate([asdict(p) for p in plugins])


@router.get(
    "/app-group-lifecycle/{plugin_id}/app-config-props",
    name="app_group_lifecycle_plugin_app_config_props",
)
def app_config_props(plugin_id: str, current_user_id: CurrentUserId) -> AppGroupLifecyclePluginAppConfig:
    _ensure_plugin(plugin_id)
    return AppGroupLifecyclePluginAppConfig.model_validate(
        {
            name: asdict(schema)
            for name, schema in get_app_group_lifecycle_plugin_app_config_properties(plugin_id).items()
        }
    )


@router.get(
    "/app-group-lifecycle/{plugin_id}/group-config-props",
    name="app_group_lifecycle_plugin_group_config_props",
)
def group_config_props(
    plugin_id: str, current_user_id: CurrentUserId, db: DbSession, app_id: str | None = None
) -> AppGroupLifecyclePluginGroupConfig:
    _ensure_plugin(plugin_id)
    # When an app_id is supplied, pass its plugin config so the schema can reflect
    # app-level constraints.
    app_plugin_data = None
    if app_id is not None:
        app = db.scalars(select(App).where(App.id == app_id).where(App.deleted_at.is_(None))).first()
        app_plugin_data = app.plugin_data if app is not None else None
    return AppGroupLifecyclePluginGroupConfig.model_validate(
        {
            name: asdict(schema)
            for name, schema in get_app_group_lifecycle_plugin_group_config_properties(
                plugin_id, app_plugin_data
            ).items()
        }
    )


@router.get(
    "/app-group-lifecycle/{plugin_id}/app-status-props",
    name="app_group_lifecycle_plugin_app_status_props",
)
def app_status_props(plugin_id: str, current_user_id: CurrentUserId) -> AppGroupLifecyclePluginAppStatus:
    _ensure_plugin(plugin_id)
    return AppGroupLifecyclePluginAppStatus.model_validate(
        {
            name: asdict(schema)
            for name, schema in get_app_group_lifecycle_plugin_app_status_properties(plugin_id).items()
        }
    )


@router.get(
    "/app-group-lifecycle/{plugin_id}/group-status-props",
    name="app_group_lifecycle_plugin_group_status_props",
)
def group_status_props(plugin_id: str, current_user_id: CurrentUserId) -> AppGroupLifecyclePluginGroupStatus:
    _ensure_plugin(plugin_id)
    return AppGroupLifecyclePluginGroupStatus.model_validate(
        {
            name: asdict(schema)
            for name, schema in get_app_group_lifecycle_plugin_group_status_properties(plugin_id).items()
        }
    )
