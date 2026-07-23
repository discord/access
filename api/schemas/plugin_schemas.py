"""Pydantic response schemas for the plugins router.

The route bodies in `api/routers/plugins.py` already produce the right wire
shape via `dataclasses.asdict(...)` over the dataclasses defined in
`api/plugins/app_group_lifecycle.py:31-62`. The schemas below mirror those
dataclasses so `response_model=...` validates them, and the `RootModel`
wrappers give the OpenAPI client a nominal type per endpoint without changing
the wire shape (top-level array / dict stays exactly as today).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, RootModel


class PluginInfo(BaseModel):
    """Mirrors `AppGroupLifecyclePluginMetadata`."""

    id: str
    display_name: str
    description: str


class PluginConfigProp(BaseModel):
    """Mirrors `AppGroupLifecyclePluginConfigProperty`."""

    display_name: str
    help_text: Optional[str] = None
    type: Literal["text", "number", "boolean"] = "text"
    default_value: Any = None
    required: bool = False
    validation: Optional[dict[str, Any]] = None
    immutable: bool = False
    suffix: Optional[str] = None


class PluginStatusProp(BaseModel):
    """Mirrors `AppGroupLifecyclePluginStatusProperty`."""

    display_name: str
    help_text: Optional[str] = None
    type: Literal["text", "number", "date", "boolean"] = "text"


class AppGroupLifecyclePlugins(RootModel[list[PluginInfo]]):
    """Wire shape for `GET /api/plugins/app-group-lifecycle`."""


class AppGroupLifecyclePluginAppConfig(RootModel[dict[str, PluginConfigProp]]):
    """Wire shape for `GET /api/plugins/app-group-lifecycle/{id}/app-config-props`."""


class AppGroupLifecyclePluginGroupConfig(RootModel[dict[str, PluginConfigProp]]):
    """Wire shape for `GET /api/plugins/app-group-lifecycle/{id}/group-config-props`."""


class AppGroupLifecyclePluginAppStatus(RootModel[dict[str, PluginStatusProp]]):
    """Wire shape for `GET /api/plugins/app-group-lifecycle/{id}/app-status-props`."""


class AppGroupLifecyclePluginGroupStatus(RootModel[dict[str, PluginStatusProp]]):
    """Wire shape for `GET /api/plugins/app-group-lifecycle/{id}/group-status-props`."""
