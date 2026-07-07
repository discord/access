from __future__ import annotations

from typing import Optional

import pluggy
import pytest

from api.plugins._async_dispatch import verify_async_impls
from api.plugins.metrics_reporter import (
    MetricsReporterPluginSpec,
    _verify_tag_forwarding,
    hookimpl,
    metrics_reporter_plugin_name,
)


def _manager_with(plugin: object) -> pluggy.PluginManager:
    pm = pluggy.PluginManager(metrics_reporter_plugin_name)
    pm.add_hookspecs(MetricsReporterPluginSpec)
    pm.register(plugin)
    return pm


def test_accepts_impl_without_defaults() -> None:
    class GoodPlugin:
        @hookimpl
        def record_counter(
            self, metric_name: str, value: float, tags: Optional[dict[str, str]], monotonic: bool = True
        ) -> None:
            pass

    _verify_tag_forwarding(_manager_with(GoodPlugin()))


def test_raises_when_tags_has_default() -> None:
    class BadPlugin:
        @hookimpl
        def record_counter(
            self, metric_name: str, value: float, tags: Optional[dict[str, str]] = None, monotonic: bool = True
        ) -> None:
            pass

    with pytest.raises(RuntimeError, match="record_counter"):
        _verify_tag_forwarding(_manager_with(BadPlugin()))


def test_verify_async_impls_rejects_sync_hook() -> None:
    class SyncPlugin:
        @hookimpl
        def record_counter(
            self, metric_name: str, value: float, tags: Optional[dict[str, str]], monotonic: bool = True
        ) -> None:
            pass

    with pytest.raises(RuntimeError, match="async def"):
        verify_async_impls(_manager_with(SyncPlugin()), ("record_counter",))


def test_verify_async_impls_accepts_async_hook() -> None:
    class AsyncPlugin:
        @hookimpl
        async def record_counter(
            self, metric_name: str, value: float, tags: Optional[dict[str, str]], monotonic: bool = True
        ) -> None:
            pass

    # Should not raise.
    verify_async_impls(_manager_with(AsyncPlugin()), ("record_counter",))
