# Slack Integration
Use this plugin to send slack notifications to users for approvals.

## Requirements
1. Setup a Slack App and get its bot user oauth token (from Slack API). It starts with `xoxb-`.
2. Ensure it has proper permissions depending on if the channel is public or private. 

- chat:write        # Must Have
- channels:read     # For public channels
- channels:write    # For public channels
- groups:read       # For private channels
- groups:write      # For private channels
- users:read        # Must Have
- users:read.email  # Must Have


3. Create a slack channel and add the Application.
- Should be under `Integrations` tab within Channel management.

4. Add your slack bot token to the .env.production file and the Channel Name.
```
SLACK_BOT_TOKEN=<your-slack-bot-token>
BASE_URL=<your-base-url>
``` 

5. Adjust your Dockerfile and add in the below; Can put it just under the Final build step and above the `ENV ..` lines.
```
# Install the plugins
WORKDIR /app/plugins
ADD ./examples/plugins/conditional_access ./conditional_access
ADD ./examples/plugins/slack ./slack
RUN pip install -r ./conditional_access/requirements.txt && pip install ./conditional_access
RUN pip install -r ./slack/requirements.txt && pip install ./slack

# Return to the app root
WORKDIR /app
```


## Testing
To run the slack integration test without docker container, do the following:
1. Setup virtual environment and install all requirements.txt
2. Export the following:
- TEST_EMAIL
- TEST_CHANNEL
- SLACK_BOT_TOKEN

Example for MacOS:
```
export TEST_EMAIL=<your-email>
export TEST_CHANNEL=<your-channel>
export SLACK_BOT_TOKEN=<your-slack-bot-token>
```
3. Run the test with python -m examples.plugins.slack.tests.test_slack_notification
