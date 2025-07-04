import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, ContextManager, Dict, Iterator, List, Optional

import pluggy

# Conditional imports for prometheus_client
try:
    import prometheus_client
    from prometheus_client import Counter, Gauge, Histogram, Summary

    PROMETHEUS_AVAILABLE = True
except ImportError:
    prometheus_client = None
    Counter = Gauge = Histogram = Summary = None
    PROMETHEUS_AVAILABLE = False

metrics_reporter_hookimpl = pluggy.HookimplMarker("access_metrics_reporter")

logger = logging.getLogger(__name__)

_prometheus_client = None
_prometheus_metrics = {}


def _init_prometheus() -> Any:
    global _prometheus_client, _prometheus_metrics

    if _prometheus_client is not None:
        return _prometheus_client

    if not PROMETHEUS_AVAILABLE:
        logger.debug("Prometheus client package not available, metrics disabled")
        return None

    try:
        # Check for multiprocess environment
        multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        if multiproc_dir:
            # Ensure directory exists
            os.makedirs(multiproc_dir, exist_ok=True)
            # Set multiprocess mode
            prometheus_client.values.ValueClass = prometheus_client.values.MultiProcessValue()
            logger.info(f"Prometheus multiprocess mode enabled with dir: {multiproc_dir}")

        # Initialize metrics registry
        _prometheus_client = prometheus_client
        _prometheus_metrics = {"counters": {}, "gauges": {}, "histograms": {}, "summaries": {}}

        # Get environment for labeling
        env = os.environ.get("FLASK_ENV", "development")
        env_tag = "prd" if env == "production" else "stg" if env == "staging" else "dev"

        logger.info(f"Prometheus initialized for metrics - env: {env_tag}")

    except Exception as e:
        logger.warning(f"Failed to initialize Prometheus: {e}")
        _prometheus_client = None

    return _prometheus_client


class PrometheusMetricsReporter:
    """Prometheus implementation of the metrics reporter plugin."""

    def __init__(self) -> None:
        self.client = _init_prometheus()
        self.global_tags: Dict[str, str] = {}
        self.batch_depth = 0
        self.batch_lock = threading.Lock()
        self.batch_buffer: List[Dict[str, Any]] = []

    def _get_metric_name(self, name: str) -> str:
        """Convert metric name to Prometheus format (snake_case)."""
        # Replace dots and dashes with underscores, ensure it starts with letter/underscore
        prometheus_name = name.replace(".", "_").replace("-", "_")
        # Ensure it starts with a letter or underscore (Prometheus requirement)
        if not prometheus_name[0].isalpha() and prometheus_name[0] != "_":
            prometheus_name = f"access_{prometheus_name}"
        # Ensure it only contains valid characters
        prometheus_name = "".join(c for c in prometheus_name if c.isalnum() or c == "_")
        return prometheus_name

    def _format_labels(self, tags: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Convert tag dict to Prometheus labels and merge with global tags."""
        if not tags:
            tags = {}

        merged_tags = {**self.global_tags, **tags}
        # Add environment label
        env = os.environ.get("FLASK_ENV", "development")
        env_tag = "prd" if env == "production" else "stg" if env == "staging" else "dev"
        merged_tags["env"] = env_tag
        merged_tags["service"] = "access"

        # Validate and clean label names (Prometheus requirements)
        cleaned_tags = {}
        for key, value in merged_tags.items():
            # Ensure label names are valid Prometheus label names
            clean_key = "".join(c for c in key if c.isalnum() or c == "_")
            if clean_key and clean_key[0].isalpha():
                cleaned_tags[clean_key] = str(value)
            else:
                logger.warning(f"Invalid label name '{key}' - skipping")

        return cleaned_tags

    def _should_buffer(self) -> bool:
        """Check if we're in a batch context."""
        with self.batch_lock:
            return self.batch_depth > 0

    def _get_or_create_counter(self, metric_name: str, labels: Dict[str, str]) -> Any:
        """Get or create a Prometheus Counter metric."""
        prometheus_name = self._get_metric_name(metric_name)

        if prometheus_name not in _prometheus_metrics["counters"]:
            if Counter:
                _prometheus_metrics["counters"][prometheus_name] = Counter(
                    prometheus_name,
                    f"Counter metric for {metric_name}",
                    labelnames=list(labels.keys()) if labels else [],
                )

        return _prometheus_metrics["counters"][prometheus_name]

    def _get_or_create_gauge(self, metric_name: str, labels: Dict[str, str]) -> Any:
        """Get or create a Prometheus Gauge metric."""
        prometheus_name = self._get_metric_name(metric_name)

        if prometheus_name not in _prometheus_metrics["gauges"]:
            if Gauge:
                _prometheus_metrics["gauges"][prometheus_name] = Gauge(
                    prometheus_name, f"Gauge metric for {metric_name}", labelnames=list(labels.keys()) if labels else []
                )

        return _prometheus_metrics["gauges"][prometheus_name]

    def _get_or_create_histogram(
        self, metric_name: str, labels: Dict[str, str], buckets: Optional[List[float]] = None
    ) -> Any:
        """Get or create a Prometheus Histogram metric."""
        prometheus_name = self._get_metric_name(metric_name)

        if prometheus_name not in _prometheus_metrics["histograms"]:
            if Histogram:
                # Use custom buckets if provided, otherwise use Prometheus defaults
                histogram_kwargs = {
                    "name": prometheus_name,
                    "help": f"Histogram metric for {metric_name}",
                    "labelnames": list(labels.keys()) if labels else [],
                }

                if buckets:
                    histogram_kwargs["buckets"] = buckets

                _prometheus_metrics["histograms"][prometheus_name] = Histogram(**histogram_kwargs)

        return _prometheus_metrics["histograms"][prometheus_name]

    def _get_or_create_summary(self, metric_name: str, labels: Dict[str, str]) -> Any:
        """Get or create a Prometheus Summary metric."""
        prometheus_name = self._get_metric_name(metric_name)

        if prometheus_name not in _prometheus_metrics["summaries"]:
            if Summary:
                _prometheus_metrics["summaries"][prometheus_name] = Summary(
                    prometheus_name,
                    f"Summary metric for {metric_name}",
                    labelnames=list(labels.keys()) if labels else [],
                )

        return _prometheus_metrics["summaries"][prometheus_name]

    @metrics_reporter_hookimpl
    def increment_counter(self, metric_name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None) -> None:
        if not self.client:
            return

        labels = self._format_labels(tags)

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append({"type": "counter", "metric": metric_name, "value": value, "labels": labels})
        else:
            counter = self._get_or_create_counter(metric_name, labels)
            if labels:
                counter.labels(**labels).inc(value)
            else:
                counter.inc(value)

    @metrics_reporter_hookimpl
    def record_gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        if not self.client:
            return

        labels = self._format_labels(tags)

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append({"type": "gauge", "metric": metric_name, "value": value, "labels": labels})
        else:
            gauge = self._get_or_create_gauge(metric_name, labels)
            if labels:
                gauge.labels(**labels).set(value)
            else:
                gauge.set(value)

    @metrics_reporter_hookimpl
    def record_timing(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        if not self.client:
            return

        labels = self._format_labels(tags)

        # Convert milliseconds to seconds for Prometheus (standard practice)
        value_seconds = value / 1000.0

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append(
                    {"type": "histogram", "metric": metric_name, "value": value_seconds, "labels": labels}
                )
        else:
            histogram = self._get_or_create_histogram(metric_name, labels)
            if labels:
                histogram.labels(**labels).observe(value_seconds)
            else:
                histogram.observe(value_seconds)

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

        labels = self._format_labels(tags)

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append(
                    {"type": "histogram", "metric": metric_name, "value": value, "labels": labels, "buckets": buckets}
                )
        else:
            histogram = self._get_or_create_histogram(metric_name, labels, buckets)
            if labels:
                histogram.labels(**labels).observe(value)
            else:
                histogram.observe(value)

    @metrics_reporter_hookimpl
    def record_summary(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        if not self.client:
            return

        labels = self._format_labels(tags)

        if self._should_buffer():
            with self.batch_lock:
                self.batch_buffer.append({"type": "summary", "metric": metric_name, "value": value, "labels": labels})
        else:
            summary = self._get_or_create_summary(metric_name, labels)
            if labels:
                summary.labels(**labels).observe(value)
            else:
                summary.observe(value)

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
        """Flush batched metrics to Prometheus."""
        if not self.client or not self.batch_buffer:
            return

        # Process all buffered metrics
        for metric in self.batch_buffer:
            metric_type = metric["type"]
            labels = metric["labels"]

            if metric_type == "counter":
                counter = self._get_or_create_counter(metric["metric"], labels)
                if labels:
                    counter.labels(**labels).inc(metric["value"])
                else:
                    counter.inc(metric["value"])
            elif metric_type == "gauge":
                gauge = self._get_or_create_gauge(metric["metric"], labels)
                if labels:
                    gauge.labels(**labels).set(metric["value"])
                else:
                    gauge.set(metric["value"])
            elif metric_type == "histogram":
                buckets = metric.get("buckets")
                histogram = self._get_or_create_histogram(metric["metric"], labels, buckets)
                if labels:
                    histogram.labels(**labels).observe(metric["value"])
                else:
                    histogram.observe(metric["value"])
            elif metric_type == "summary":
                summary = self._get_or_create_summary(metric["metric"], labels)
                if labels:
                    summary.labels(**labels).observe(metric["value"])
                else:
                    summary.observe(metric["value"])

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

        logger.debug("Metrics flushed to Prometheus")


# Plugin registration
prometheus_metrics_plugin = PrometheusMetricsReporter()
