# Discord Access Datadog Metrics Plugin

This plugin integrates Discord access metrics with Datadog, allowing users to track and monitor access request patterns, approval rates, and system health metrics.

## Installation

Update the Dockerfile used to build the App container includes the following section for installing the metrics plugin before starting gunicorn:

```dockerfile
# Add the specific plugins and install metrics
WORKDIR /app/plugins
ADD ./examples/plugins/metrics_reporter ./metrics_reporter
RUN pip install -r ./metrics_reporter/requirements.txt && pip install ./metrics_reporter

# Reset working directory
WORKDIR /app

ENV FLASK_ENV production
ENV FLASK_APP api.app:create_app
ENV SENTRY_RELEASE $SENTRY_RELEASE

EXPOSE 3000

CMD ["gunicorn", "-w", "4", "-t", "600", "-b", ":3000", "--access-logfile", "-", "api.wsgi:app"]
```

## Build the Docker image, run and test

You may use the original Discord Access container build processes from the primary README.md:
```bash
docker compose up --build
```

Verify metrics collection is working as designed.

## Configuration

The following environment variables need to be configured for the Datadog metrics plugin to work properly:

### Required Environment Variables

- `FLASK_ENV`: Application environment (e.g., `production`, `staging`, `development`)
  - Used to determine the environment tag for metrics
  - Maps to: `production` → `prd`, `staging` → `stg`, other → `dev`

### Optional Environment Variables

- `STATSD_HOST_IP`: IP address of the StatsD/DogStatsD server
  - If not set, falls back to `DD_AGENT_HOST`
  - Default: `127.0.0.1`

- `DD_AGENT_HOST`: Datadog Agent host address
  - Used when `STATSD_HOST_IP` is not provided
  - Default: `127.0.0.1`

- `DD_DOGSTATSD_PORT`: Port for DogStatsD communication
  - Default: `8125`

## Development

To contribute to the development of this plugin, please follow the standard Git workflow:

1. Fork the repository.
2. Create a new branch for your feature or bug fix.
3. Make your changes and commit them.
4. Push your branch and create a pull request.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

