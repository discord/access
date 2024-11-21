# Discord Access Slack Notifications Plugin

This plugin integrates Discord access notifications with Slack, allowing users to receive updates and alerts regarding their access requests and expirations directly in Slack.

## Installation

Update the Dockerfile used to build the App container includes the following section for installing the notifications plugin before starting gunicorn:
```dockerfile
# Add the specific plugins and install notifications for both final stages
WORKDIR /app/plugins
ADD ./examples/plugins/notifications_slack ./notifications_slack
RUN pip install -r ./notifications_slack/requirements.txt && pip install ./notifications_slack

# Reset working directory for both final stages
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

Verify Slack notifications are work as designed.

## Plugin Configuration

The plugin requires the following environment variables to be set:

- `SLACK_BOT_TOKEN`: The token for your Slack bot.
- `SLACK_SIGNING_SECRET`: The signing secret for your Slack app.
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
