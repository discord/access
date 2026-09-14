# Prometheus Metrics Reporter Plugin

A Prometheus-compatible metrics reporter plugin for the Access Management System. This plugin integrates with the `prometheus_client` library to expose metrics in Prometheus format.

## Features

- **Counter Metrics**: Track cumulative values (e.g., request counts, errors)
- **Gauge Metrics**: Track current values (e.g., active connections, queue size)
- **Histogram Metrics**: Track distributions of values (e.g., response times, request sizes)
- **Summary Metrics**: Track quantiles of values (e.g., percentiles of response times)
- **Batch Processing**: Efficiently batch multiple metrics for better performance
- **Label Support**: Add custom labels to metrics for better filtering and grouping
- **Environment Detection**: Automatic environment labeling (dev/stg/prd)
- **Thread Safety**: Thread-safe implementation for concurrent access

## Installation

### Prerequisites

- Python 3.7+
- Access Management System with plugin support
- `prometheus_client` library

### Install the Plugin

1. **Clone or download the plugin files** to your desired location

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install the plugin**:
   ```bash
   pip install -e .
   ```

## Configuration

### Environment Variables

The plugin uses the following environment variables for configuration:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `FLASK_ENV` | Environment name for metric labeling | `development` | No |
| `PROMETHEUS_MULTIPROC_DIR` | Directory for multiprocess metrics | None | No* |
| `PROMETHEUS_DISABLE_CREATED_SERIES` | Disable created series metrics | `False` | No |

*Required if using multiple processes (e.g., Gunicorn with multiple workers)

### Environment Labeling

The plugin automatically adds environment labels based on `FLASK_ENV`:

- `development` → `env=dev`
- `staging` → `env=stg` 
- `production` → `env=prd`
- Any other value → `env=dev`

### Global Tags

You can set global tags that will be included with all metrics:

```python
from access.plugins.metrics_reporter import metrics_reporter

# Set global tags
metrics_reporter.set_global_tags({
    "service": "access",
    "version": "1.0.0",
    "region": "us-west-2"
})
```

## Usage

### Basic Usage

The plugin automatically registers with Access when installed. Metrics will be exposed at the `/metrics` endpoint in Prometheus format.

### Metric Types

#### Counters

```python
# Increment a counter
metrics_reporter.increment_counter("requests_total", tags={"endpoint": "/api/users"})

# Increment with custom value
metrics_reporter.increment_counter("bytes_processed", value=1024, tags={"type": "upload"})
```

#### Gauges

```python
# Set a gauge value
metrics_reporter.record_gauge("active_connections", 42, tags={"pool": "database"})

# Update queue size
metrics_reporter.record_gauge("queue_size", 15, tags={"queue": "email"})
```

#### Histograms

```python
# Record timing (automatically converts ms to seconds)
metrics_reporter.record_timing("request_duration", 150.5, tags={"endpoint": "/api/users"})

# Record custom histogram with buckets
metrics_reporter.record_histogram(
    "request_size", 
    2048, 
    tags={"method": "POST"},
    buckets=[100, 500, 1000, 5000, 10000]
)
```

#### Summaries

```python
# Record summary metric
metrics_reporter.record_summary("response_time", 0.25, tags={"service": "auth"})
```

### Batch Processing

For better performance when recording multiple metrics:

```python
with metrics_reporter.batch_metrics():
    metrics_reporter.increment_counter("requests_total", tags={"endpoint": "/api/users"})
    metrics_reporter.record_gauge("active_connections", 42)
    metrics_reporter.record_timing("request_duration", 150.5)
```

### Manual Flush

Force flush any buffered metrics:

```python
metrics_reporter.flush()
```

## Prometheus Integration

### Exposing Metrics

The plugin automatically exposes metrics at `/metrics` endpoint. Ensure your Prometheus server is configured to scrape this endpoint.

### Example Prometheus Configuration

```yaml
scrape_configs:
  - job_name: 'access-management'
    static_configs:
      - targets: ['localhost:5000']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

### Metric Naming

All metrics are automatically converted to Prometheus naming conventions:
- Dots and dashes are replaced with underscores
- Invalid characters are removed
- Metrics starting with numbers are prefixed with `access_`

Examples:
- `requests.total` → `requests_total`
- `api-errors` → `api_errors`
- `1st_request` → `access_1st_request`

### Label Validation

The plugin automatically validates and cleans label names:
- Only alphanumeric characters and underscores are allowed
- Labels must start with a letter
- Invalid labels are logged and skipped

## Troubleshooting

### Common Issues

1. **Import Error**: Ensure `prometheus_client` is installed
   ```bash
   pip install prometheus_client>=0.17.0
   ```

2. **Multiprocess Issues**: Set `PROMETHEUS_MULTIPROC_DIR` environment variable
   ```bash
   export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
   ```

3. **Permission Errors**: Ensure the multiprocess directory is writable
   ```bash
   mkdir -p /tmp/prometheus_multiproc
   chmod 755 /tmp/prometheus_multiproc
   ```

4. **Metric Name Conflicts**: Check for duplicate metric names with different label sets

### Debugging

Enable debug logging to see metric operations:

```python
import logging
logging.getLogger('metrics_reporter').setLevel(logging.DEBUG)
```

### Testing

Test the metrics endpoint:

```bash
curl http://localhost:5000/metrics
```

You should see Prometheus-formatted metrics output.

## Development

### Running Tests

```bash
python -m pytest tests/
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

This plugin is part of the Access Management System and follows the same license terms.
