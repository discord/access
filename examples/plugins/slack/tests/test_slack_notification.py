import os

from ..slack_notifications import slack

# Constants
TEST_DM_MESSAGE = "🎉 Test DM with a *bold* link: <https://google.com|Click here>"
TEST_CHANNEL_MESSAGE = "📢 Test channel message from the notifications service"
TEST_CHANNEL = os.getenv("TEST_CHANNEL")
TEST_EMAIL = os.getenv("TEST_EMAIL")
# Slack Application token must also be set; see slack_notifications.py for SLACK_BOT_TOKEN

if not TEST_CHANNEL:
    raise ValueError("TEST_CHANNEL environment variable is required")
if not TEST_EMAIL:
    raise ValueError("TEST_EMAIL environment variable is required")


def test_direct_message():
    # Test sending a DM to yourself
    result = slack.send_message(
        message=TEST_DM_MESSAGE,
        user_email=TEST_EMAIL,
    )
    print(f"DM sent successfully: {result}")


def test_channel_message():
    # Test sending to a channel
    result = slack.send_message(
        message=TEST_CHANNEL_MESSAGE,
        channel=TEST_CHANNEL,
    )
    print(f"Channel message sent successfully: {result}")


if __name__ == "__main__":
    test_direct_message()
    test_channel_message()
