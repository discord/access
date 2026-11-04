from __future__ import annotations

from typing import Optional

import pluggy
import pytest

from api.plugins._async_dispatch import verify_async_impls
from api.plugins.metrics_reporter import (
    MetricsReporterPluginSpec,
    hookimpl,
    metrics_reporter_plugin_name,
)


def _manager_with(plugin: object) -> pluggy.PluginManager:
    pm = pluggy.PluginManager(metrics_reporter_plugin_name)
    pm.add_hookspecs(MetricsReporterPluginSpec)
    pm.register(plugin)
    return pm


def test_impl_defaults_receive_caller_values() -> None:
    """An impl that defaults value and tags still receives what the caller passes."""
    received: list[tuple[float, Optional[dict[str, str]], bool]] = []

    class DefaultingPlugin:
        @hookimpl
        def record_counter(
            self,
            metric_name: str,
            value: float = 0.0,
            tags: Optional[dict[str, str]] = None,
            monotonic: bool = False,
        ) -> None:
            received.append((value, tags, monotonic))

    pm = _manager_with(DefaultingPlugin())
    pm.hook.record_counter(metric_name="requests", value=3.0, tags={"route": "/api"}, monotonic=True)

    assert received == [(3.0, {"route": "/api"}, True)]


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
