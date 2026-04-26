"""Plugins router. Read-only metadata about app group lifecycle plugins."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api.auth.dependencies import CurrentUserId
from api.plugins.app_group_lifecycle import get_app_group_lifecycle_plugins

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _serialize_plugin(p: Any) -> dict[str, Any]:
    return {
        "id": getattr(p, "id", None),
        "name": getattr(p, "name", None),
        "description": getattr(p, "description", None),
    }


@router.get("/app-group-lifecycle", name="app_group_lifecycle_plugins")
def list_plugins(current_user_id: CurrentUserId) -> list[dict[str, Any]]:
    return [_serialize_plugin(p) for p in get_app_group_lifecycle_plugins()]


def _get_plugin_or_404(plugin_id: str) -> Any:
    for p in get_app_group_lifecycle_plugins():
        if getattr(p, "id", None) == plugin_id:
            return p
    raise HTTPException(404, "Plugin not found")


@router.get(
    "/app-group-lifecycle/{plugin_id}/app-config-props",
    name="app_group_lifecycle_plugin_app_config_props",
)
def app_config_props(plugin_id: str, current_user_id: CurrentUserId) -> list[dict[str, Any]]:
    plugin = _get_plugin_or_404(plugin_id)
    props = getattr(plugin, "app_config_properties", None) or []
    return [getattr(p, "__dict__", p) for p in props]


@router.get(
    "/app-group-lifecycle/{plugin_id}/group-config-props",
    name="app_group_lifecycle_plugin_group_config_props",
)
def group_config_props(plugin_id: str, current_user_id: CurrentUserId) -> list[dict[str, Any]]:
    plugin = _get_plugin_or_404(plugin_id)
    props = getattr(plugin, "group_config_properties", None) or []
    return [getattr(p, "__dict__", p) for p in props]


@router.get(
    "/app-group-lifecycle/{plugin_id}/app-status-props",
    name="app_group_lifecycle_plugin_app_status_props",
)
def app_status_props(plugin_id: str, current_user_id: CurrentUserId) -> list[dict[str, Any]]:
    plugin = _get_plugin_or_404(plugin_id)
    props = getattr(plugin, "app_status_properties", None) or []
    return [getattr(p, "__dict__", p) for p in props]


@router.get(
    "/app-group-lifecycle/{plugin_id}/group-status-props",
    name="app_group_lifecycle_plugin_group_status_props",
)
def group_status_props(plugin_id: str, current_user_id: CurrentUserId) -> list[dict[str, Any]]:
    plugin = _get_plugin_or_404(plugin_id)
    props = getattr(plugin, "group_status_properties", None) or []
    return [getattr(p, "__dict__", p) for p in props]
