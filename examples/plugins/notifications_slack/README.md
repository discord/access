# Discord Access Slack Notifications Plugin

This plugin integrates Discord access notifications with Slack, allowing users to receive updates and alerts regarding their access requests and expirations directly in Slack.

## Installation

This plugin's source ships in the Access build context under
`examples/plugins/`, but the default image does **not** install it. Enable it at
build time with its build arg (default `false`):

```bash
docker build --build-arg INSTALL_SLACK_NOTIFICATIONS_PLUGIN=true .
# or, with docker compose:
docker compose build --build-arg INSTALL_SLACK_NOTIFICATIONS_PLUGIN=true
```

The image installs the plugin (and its `requirements.txt`, which carries
`slack-sdk` and `aiohttp`) into the `uv` virtualenv (`/app/.venv`) with
`uv pip install` — the venv has no `pip`, and plain `pip` would install into the
system interpreter where the running app won't find it. See
[the plugins README](../README.md) for every plugin's build arg and for baking
in a plugin of your own.

## Build the Docker image, run and test

Build with the arg above, then run as usual:
```bash
docker compose up --build
```

Verify Slack notifications work as designed.

## Plugin Configuration

The plugin requires the following environment variables to be set:

- `SLACK_BOT_TOKEN`: The token for your Slack bot.
- `SLACK_ALERTS_CHANNEL`: The channel where alerts will be sent. String name like `#alerts-discord-access`
- `CLIENT_ORIGIN_URL`: The base URL for your application.

## Plugin Structure

The plugin consists of the following components:

- **Notifications Slack**: This component handles sending notifications to Slack when access requests are created, completed, or expiring.

## Usage

After installing and setting up the plugin, it automatically sends notifications to the relevant users and owners when an access request is created, completed, or is about to expire. You can also choose to send these notifications to a designated Slack alerts channel for logging and better visibility by setting SLACK_ALERTS_CHANNEL.

## Development

To contribute to the development of this plugin, please follow the standard Git workflow:

1. Fork the repository.
2. Create a new branch for your feature or bug fix.
3. Make your changes and commit them.
4. Push your branch and create a pull request.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
