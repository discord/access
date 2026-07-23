# Discord Access Datadog Metrics Plugin

This plugin integrates Discord access metrics with Datadog, allowing users to track and monitor access request patterns, approval rates, and system health metrics.

## Installation

This plugin's source ships in the Access build context under
`examples/plugins/`, but the default image does **not** install it. Enable it at
build time with its build arg (default `false`):

```bash
docker build --build-arg INSTALL_DATADOG_METRICS_PLUGIN=true .
# or, with docker compose:
docker compose build --build-arg INSTALL_DATADOG_METRICS_PLUGIN=true
```

The image installs the plugin (and its `requirements.txt`) into the `uv`
virtualenv (`/app/.venv`) with `uv pip install` — the venv has no `pip`, and
plain `pip` would install into the system interpreter where the running app
won't find it. See [the plugins README](../README.md) for every plugin's build
arg and for baking in a plugin of your own.

## Build the Docker image, run and test

Build with the arg above, then run as usual:
```bash
docker compose up --build
```

Verify metrics collection is working as designed.

## Configuration

The following environment variables need to be configured for the Datadog metrics plugin to work properly:

### Required Environment Variables

- `ENV`: Application environment (e.g., `production`, `staging`, `development`)
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

