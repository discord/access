import logging
import sys
from typing import ContextManager

import pluggy

metrics_reporter_plugin_name = "access_metrics_reporter"
hookspec = pluggy.HookspecMarker(metrics_reporter_plugin_name)
hookimpl = pluggy.HookimplMarker(metrics_reporter_plugin_name)

_cached_metrics_reporter_hook: pluggy.HookRelay | None = None

logger = logging.getLogger(__name__)


class MetricsReporterPluginSpec:
    @hookspec
    def record_counter(
        self,
        metric_name: str,
        value: float = 1.0,
        tags: dict = None,
        monotonic: bool = True,
    ) -> None:
        """
        Record a counter metric value.

        Args:
            metric_name: The metric name
            value: The value to add (default 1.0)
            tags: Optional tags
            monotonic: If True, counter only increases. If False, can decrease.
        """

    @hookspec
    def record_gauge(
        self,
        metric_name: str,
        value: float,
        tags: dict = None,
    ) -> None:
        """Record a gauge metric value (snapshot/current value)."""

    @hookspec
    def record_histogram(
        self,
        metric_name: str,
        value: float,
        tags: dict = None,
        buckets: list = None,
    ) -> None:
        """
        Record a value in a histogram/distribution.

        Args:
            metric_name: The metric name
            value: The value to record
            tags: Optional tags
            buckets: Optional bucket boundaries for the histogram
        """

    @hookspec
    def record_summary(
        self,
        metric_name: str,
        value: float,
        tags: dict = None,
    ) -> None:
        """
        Record a value for summary statistics (percentiles, min, max, etc).
        This is similar to histogram but may be implemented differently by backends.
        """

    @hookspec
    def batch_metrics(self) -> ContextManager:
        """
        Context manager for batching multiple metric operations.

        Returns a context manager that batches metric operations for efficiency.
        Particularly useful for HTTP-based backends to reduce network calls.

        Example:
            with metrics.batch_metrics():
                metrics.record_counter("requests", 1)
                metrics.record_gauge("queue_size", 42)
                metrics.record_histogram("response_time", 0.123)
            # All metrics sent in one batch here
        """

    @hookspec
    def set_global_tags(
        self,
        tags: dict,
    ) -> None:
        """Set global tags to be included with all metrics."""

    @hookspec
    def flush(self) -> None:
        """Force flush any buffered metrics to the backend."""


def get_metrics_reporter_hook() -> pluggy.HookRelay:
    global _cached_metrics_reporter_hook

    if _cached_metrics_reporter_hook is not None:
        return _cached_metrics_reporter_hook

    pm = pluggy.PluginManager(metrics_reporter_plugin_name)
    pm.add_hookspecs(MetricsReporterPluginSpec)

    # Register the hook wrappers
    pm.register(sys.modules[__name__])

    count = pm.load_setuptools_entrypoints(metrics_reporter_plugin_name)
    print(f"Count of loaded metrics reporter plugins: {count}")
    _cached_metrics_reporter_hook = pm.hook

    return _cached_metrics_reporter_hook
