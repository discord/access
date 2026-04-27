"""Plugins router. Read-only metadata about app group lifecycle plugins."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from api.auth.dependencies import CurrentUserId
from api.plugins.app_group_lifecycle import (
    get_app_group_lifecycle_plugin_app_config_properties,
    get_app_group_lifecycle_plugin_app_status_properties,
    get_app_group_lifecycle_plugin_group_config_properties,
    get_app_group_lifecycle_plugin_group_status_properties,
    get_app_group_lifecycle_plugins,
)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _ensure_plugin(plugin_id: str) -> None:
    plugins = [p.id for p in get_app_group_lifecycle_plugins()]
    if plugin_id not in plugins:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")


@router.get("/app-group-lifecycle", name="app_group_lifecycle_plugins")
def list_plugins(current_user_id: CurrentUserId) -> list[dict[str, Any]]:
    plugins = sorted(get_app_group_lifecycle_plugins(), key=lambda p: p.display_name.lower())
    return [asdict(p) for p in plugins]


@router.get(
    "/app-group-lifecycle/{plugin_id}/app-config-props",
    name="app_group_lifecycle_plugin_app_config_props",
)
def app_config_props(plugin_id: str, current_user_id: CurrentUserId) -> dict[str, Any]:
    _ensure_plugin(plugin_id)
    return {name: asdict(schema) for name, schema in get_app_group_lifecycle_plugin_app_config_properties(plugin_id).items()}


@router.get(
    "/app-group-lifecycle/{plugin_id}/group-config-props",
    name="app_group_lifecycle_plugin_group_config_props",
)
def group_config_props(plugin_id: str, current_user_id: CurrentUserId) -> dict[str, Any]:
    _ensure_plugin(plugin_id)
    return {name: asdict(schema) for name, schema in get_app_group_lifecycle_plugin_group_config_properties(plugin_id).items()}


@router.get(
    "/app-group-lifecycle/{plugin_id}/app-status-props",
    name="app_group_lifecycle_plugin_app_status_props",
)
def app_status_props(plugin_id: str, current_user_id: CurrentUserId) -> dict[str, Any]:
    _ensure_plugin(plugin_id)
    return {name: asdict(schema) for name, schema in get_app_group_lifecycle_plugin_app_status_properties(plugin_id).items()}


@router.get(
    "/app-group-lifecycle/{plugin_id}/group-status-props",
    name="app_group_lifecycle_plugin_group_status_props",
)
def group_status_props(plugin_id: str, current_user_id: CurrentUserId) -> dict[str, Any]:
    _ensure_plugin(plugin_id)
    return {name: asdict(schema) for name, schema in get_app_group_lifecycle_plugin_group_status_properties(plugin_id).items()}
