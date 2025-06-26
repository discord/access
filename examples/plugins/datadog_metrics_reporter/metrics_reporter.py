import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, ContextManager, Dict, Iterator, List, Optional

import pluggy

metrics_reporter_hookimpl = pluggy.HookimplMarker("access_metrics_reporter")

logger = logging.getLogger(__name__)

_datadog_client = None


def _init_datadog() -> Any:
    global _datadog_client

    if _datadog_client is not None:
        return _datadog_client

    try:
        import datadog

        statsd_host_ip = os.environ.get("STATSD_HOST_IP")
        env = os.environ.get("FLASK_ENV", "development")

        statsd_host = statsd_host_ip if statsd_host_ip else os.environ.get("DD_AGENT_HOST", "127.0.0.1")
        statsd_port = int(os.environ.get("DD_DOGSTATSD_PORT", 8125))

        # Determine environment tag
        env_tag = "prd" if env == "production" else "stg" if env == "staging" else "dev"

        datadog.initialize(
            statsd_host=statsd_host,
            statsd_port=statsd_port,
            statsd_namespace="access",
        )

        datadog.statsd.constant_tags = ["service:access", f"env:{env_tag}"]

        _datadog_client = datadog.statsd
        logger.info(f"Datadog initialized for metrics - host: {statsd_host}:{statsd_port}, env: {env_tag}")

    except ImportError:
        logger.debug("Datadog package not available, metrics disabled")
        _datadog_client = None
    except Exception as e:
        logger.warning(f"Failed to initialize Datadog: {e}")
        _datadog_client = None

    return _datadog_client


class DatadogMetricsReporter:
    """Datadog implementation of the metrics reporter plugin."""

    def __init__(self) -> None:
        self.client = _init_datadog()
        self.global_tags: Dict[str, str] = {}
        self.batch_depth = 0
        self.batch_lock = threading.Lock()
        self.batch_buffer: List[Dict[str, Any]] = []

    def _format_tags(self, tags: Optional[Dict[str, str]] = None) -> List[str]:
        """Convert tag dict to Datadog format and merge with global tags."""
        if not tags:
            tags = {}

        merged_tags = {**self.global_tags, **tags}
        return [f"{k}:{v}" for k, v in merged_tags.items()]

    def _should_buffer(self) -> bool:
        """Check if we're in a batch context."""
        with self.batch_lock:
            return self.batch_depth > 0

    @metrics_reporter_hookimpl
    def record_counter(
        self, metric_name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None, monotonic: bool = True
    ) -> None:
        if not self.client:
            return

        formatted_tags = self._format_tags(tags)

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append(
                    {
                        "type": "counter",
                        "metric": metric_name,
                        "value": value,
                        "tags": formatted_tags,
                        "monotonic": monotonic,
                    }
                )
        else:
            self.client.increment(metric_name, value=value, tags=formatted_tags)

    @metrics_reporter_hookimpl
    def record_gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        if not self.client:
            return

        formatted_tags = self._format_tags(tags)

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append(
                    {"type": "gauge", "metric": metric_name, "value": value, "tags": formatted_tags}
                )
        else:
            self.client.gauge(metric_name, value=value, tags=formatted_tags)

    @metrics_reporter_hookimpl
    def record_histogram(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
        buckets: Optional[List[float]] = None,
    ) -> None:
        if not self.client:
            return

        formatted_tags = self._format_tags(tags)

        # Note: DogStatsD doesn't support custom buckets directly
        # Buckets are configured on the Datadog Agent side
        if buckets:
            logger.debug(f"Custom buckets not supported by DogStatsD for {metric_name}")

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append(
                    {"type": "histogram", "metric": metric_name, "value": value, "tags": formatted_tags}
                )
        else:
            self.client.histogram(metric_name, value=value, tags=formatted_tags)

    @metrics_reporter_hookimpl
    def record_summary(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        if not self.client:
            return

        formatted_tags = self._format_tags(tags)

        # DogStatsD uses distribution for summary-like metrics with server-side aggregation
        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append(
                    {"type": "distribution", "metric": metric_name, "value": value, "tags": formatted_tags}
                )
        else:
            self.client.distribution(metric_name, value=value, tags=formatted_tags)

    @metrics_reporter_hookimpl
    def batch_metrics(self) -> ContextManager[None]:
        @contextmanager
        def _batch_context() -> Iterator[None]:
            with self.batch_lock:
                self.batch_depth += 1
            try:
                yield
            finally:
                with self.batch_lock:
                    self.batch_depth -= 1
                    if self.batch_depth == 0:
                        self._flush_batch()

        return _batch_context()

    def _flush_batch(self) -> None:
        """Flush batched metrics to DogStatsD."""
        if not self.client or not self.batch_buffer:
            return

        # Process all buffered metrics
        # Note: DogStatsD itself batches over UDP, so we just send them sequentially
        for metric in self.batch_buffer:
            metric_type = metric["type"]

            if metric_type == "counter":
                self.client.increment(metric["metric"], value=metric["value"], tags=metric["tags"])
            elif metric_type == "gauge":
                self.client.gauge(metric["metric"], value=metric["value"], tags=metric["tags"])
            elif metric_type == "histogram":
                self.client.histogram(metric["metric"], value=metric["value"], tags=metric["tags"])
            elif metric_type == "distribution":
                self.client.distribution(metric["metric"], value=metric["value"], tags=metric["tags"])

        self.batch_buffer.clear()

    @metrics_reporter_hookimpl
    def set_global_tags(self, tags: Dict[str, str]) -> None:
        """Set global tags to be included with all metrics."""
        self.global_tags.update(tags)

    @metrics_reporter_hookimpl
    def flush(self) -> None:
        """Force flush any buffered metrics."""
        if not self.client:
            return

        with self.batch_lock:
            if self.batch_buffer:
                self._flush_batch()

        # DogStatsD doesn't have an explicit flush method since it uses UDP
        # but we can log for debugging
        logger.debug("Metrics flushed to DogStatsD")


# Plugin registration
datadog_metrics_plugin = DatadogMetricsReporter()
