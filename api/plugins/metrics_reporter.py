import inspect
import logging
from typing import AsyncContextManager, Dict, List, Optional

import pluggy

from api.plugins._async_dispatch import verify_async_impls

metrics_reporter_plugin_name = "access_metrics_reporter"
hookspec = pluggy.HookspecMarker(metrics_reporter_plugin_name)
hookimpl = pluggy.HookimplMarker(metrics_reporter_plugin_name)

_cached_metrics_reporter_hook: Optional[pluggy.HookRelay] = None

# Hooks whose value/tags carry per-call data. pluggy only forwards a caller
# argument to an implementation when that parameter has no default, so an impl
# that defaults value or tags silently discards the caller's metric data.
_TAG_FORWARDING_HOOKS = ("record_counter", "record_gauge", "record_histogram", "record_summary")
_MUST_NOT_DEFAULT = ("value", "tags")

# Awaited metric hooks — must be `async def`. `batch_metrics` is
# excluded: it returns an async context manager (used via `async with`), not a
# coroutine, so it would fail the coroutine-function check.
_ASYNC_HOOKS = ("record_counter", "record_gauge", "record_histogram", "record_summary", "set_global_tags", "flush")

logger = logging.getLogger(__name__)


class MetricsReporterPluginSpec:
    @hookspec
    async def record_counter(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]],
        monotonic: bool = True,
    ) -> None:
        """
        Record a counter metric value.

        Args:
            metric_name: The metric name
            value: The value to add
            tags: Tags for the metric (pass {} or None for no tags)
            monotonic: If True, counter only increases. If False, can decrease.

        Implementations must declare value and tags without defaults, or pluggy
        silently drops the caller's values (see _verify_tag_forwarding).
        """

    @hookspec
    async def record_gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]],
    ) -> None:
        """Record a gauge metric value (snapshot/current value)."""

    @hookspec
    async def record_histogram(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]],
        buckets: Optional[List[float]] = None,
    ) -> None:
        """
        Record a value in a histogram/distribution.

        Args:
            metric_name: The metric name
            value: The value to record
            tags: Tags (pass {} or None for no tags)
            buckets: Optional bucket boundaries for the histogram

        Note: see record_counter; tags is required so pluggy forwards it.
        """

    @hookspec
    async def record_summary(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]],
    ) -> None:
        """
        Record a value for summary statistics (percentiles, min, max, etc).
        This is similar to histogram but may be implemented differently by backends.

        Note: see record_counter; tags is required so pluggy forwards it.
        """

    @hookspec
    def batch_metrics(self) -> AsyncContextManager[None]:
        """
        Async context manager for batching multiple metric operations.

        Returns an async context manager that batches metric operations for
        efficiency. Particularly useful for HTTP-based backends to reduce network
        calls.

        Example:
            async with metrics.batch_metrics():
                await metrics.record_counter("requests", 1, tags={"method": "GET"})
                await metrics.record_gauge("queue_size", 42, tags=None)
                await metrics.record_histogram("response_time", 0.123, tags={"route": "/api"})
            # All metrics sent in one batch here
        """
        return NotImplemented

    @hookspec
    async def set_global_tags(
        self,
        tags: Dict[str, str],
    ) -> None:
        """Set global tags to be included with all metrics."""

    @hookspec
    async def flush(self) -> None:
        """Force flush any buffered metrics to the backend."""


def _verify_tag_forwarding(pm: pluggy.PluginManager) -> None:
    """Fail fast if a registered implementation would make pluggy silently drop
    a metric's value or tags.

    pluggy forwards a caller argument to an implementation only when that
    parameter has no default. An impl that defaults value or tags therefore
    discards the caller's data without error. Raise so an org that upgrades
    Access without updating its plugin sees the problem at startup.
    """
    offenders = []
    for hook_name in _TAG_FORWARDING_HOOKS:
        caller = getattr(pm.hook, hook_name, None)
        if caller is None:
            continue
        for impl in caller.get_hookimpls():
            params = inspect.signature(impl.function).parameters
            defaulted = [
                arg for arg in _MUST_NOT_DEFAULT if arg in params and params[arg].default is not inspect.Parameter.empty
            ]
            if defaulted:
                offenders.append(f"{pm.get_name(impl.plugin)}.{hook_name} (defaults: {', '.join(defaulted)})")
    if offenders:
        raise RuntimeError(
            "metrics_reporter implementations declare defaults for arguments that "
            "pluggy then silently drops, discarding the metric's value/tags. Remove "
            "the defaults from these implementation parameters: " + "; ".join(offenders)
        )


def get_metrics_reporter_hook() -> pluggy.HookRelay:
    global _cached_metrics_reporter_hook

    if _cached_metrics_reporter_hook is not None:
        return _cached_metrics_reporter_hook

    pm = pluggy.PluginManager(metrics_reporter_plugin_name)
    pm.add_hookspecs(MetricsReporterPluginSpec)

    count = pm.load_setuptools_entrypoints(metrics_reporter_plugin_name)
    logger.debug(f"Count of loaded metrics reporter plugins: {count}")
    _verify_tag_forwarding(pm)
    verify_async_impls(pm, _ASYNC_HOOKS)
    _cached_metrics_reporter_hook = pm.hook

    return _cached_metrics_reporter_hook
