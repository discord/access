from __future__ import print_function

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import pluggy
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from api.models import AccessRequest, OktaGroup, OktaUser, RoleGroup

# Constants
DEFAULT_SLACK_CHANNEL = "#access-notifications"
DEFAULT_TIMEOUT_SECONDS = 30
SLACK_MESSAGE_TYPE = "mrkdwn"  # Slack's markdown format type
SLACK_BLOCK_TYPE = "section"

notification_hook_impl = pluggy.HookimplMarker("access_notifications")
logger = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, token: str, default_channel: str = DEFAULT_SLACK_CHANNEL):
        self.client = WebClient(
            token=token,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        self.default_channel = default_channel

    def send_message(
        self,
        message: str,
        channel: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> bool:
        try:
            if user_email:
                # Look up user ID from email
                user_response = self.client.users_lookupByEmail(email=user_email)
                if not user_response["ok"]:
                    logger.error(f"Failed to look up user by email: {user_email}")
                    return False

                user_id = user_response["user"]["id"]

                # Open DM channel
                conversation = self.client.conversations_open(users=[user_id])
                if not conversation["ok"]:
                    logger.error(f"Failed to open DM with user: {user_email}")
                    return False

                channel_id = conversation["channel"]["id"]
            else:
                channel_id = channel or self.default_channel

            # Send the message
            blocks = [{"type": SLACK_BLOCK_TYPE, "text": {"type": SLACK_MESSAGE_TYPE, "text": message}}]

            response = self.client.chat_postMessage(
                channel=channel_id,
                text=message,  # Fallback text
                blocks=blocks,
            )

            return response["ok"]

        except SlackApiError as e:
            logger.error(f"Failed to send Slack message: {str(e)}")
            return False


def get_slack_client():
    """Get or create the Slack client instance. Raises error if SLACK_BOT_TOKEN is not set."""
    if not hasattr(get_slack_client, "_instance"):
        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "SLACK_BOT_TOKEN environment variable is not set. "
                "This is required for the Slack notifications plugin to function."
            )

        get_slack_client._instance = SlackNotifier(
            token=token,
            default_channel=DEFAULT_SLACK_CHANNEL,
        )
    return get_slack_client._instance


def get_base_url() -> str:
    # First check for explicit BASE_URL environment variable
    base_url = os.environ.get("BASE_URL")
    if base_url:
        return base_url

    # Fall back to environment-based logic if BASE_URL not set
    env = os.environ.get("FLASK_ENV", "development")
    if env == "production":
        return "https://example.com"
    else:
        return "http://localhost:3000"


def parse_dates(comparison_date: datetime, owner: bool) -> str:
    if not comparison_date:
        return "soon"

    just_date = comparison_date.date()
    today = date.today()

    prev_monday = today - timedelta(days=today.weekday())

    if owner:  # 'round' dates to previous Monday
        if prev_monday <= just_date and just_date < prev_monday + timedelta(weeks=1):
            return "this week"
        elif prev_monday + timedelta(weeks=1) <= just_date and just_date < prev_monday + timedelta(weeks=2):
            return "next week"
        elif prev_monday + timedelta(weeks=2) <= just_date and just_date < prev_monday + timedelta(weeks=3):
            return "in two weeks"
        elif prev_monday + timedelta(weeks=3) <= just_date and just_date < prev_monday + timedelta(weeks=4):
            return "in three weeks"
        elif prev_monday + timedelta(weeks=4) <= just_date and just_date < prev_monday + timedelta(weeks=5):
            return "in four weeks"
    else:
        if just_date == today + timedelta(days=1):  # one day before
            return "tomorrow"
        elif just_date == today + timedelta(weeks=1):  # one week before
            return "in a week"
        elif just_date == today + timedelta(weeks=2):  # 2 weeks before
            return "in two weeks"

    return "soon"


@notification_hook_impl
def access_request_created(
    access_request: AccessRequest, group: OktaGroup, requester: OktaUser, approvers: list[OktaUser]
) -> None:
    """Notify all the approvers of the access request through a notification"""
    type_of_access = "ownership of" if access_request.request_ownership else "membership to"
    access_request_url = get_base_url() + f"/requests/{access_request.id}"

    approver_message = (
        f"{requester.email} has requested {type_of_access} {group.name}.\n\n"
        f"[View request to approve or reject]({access_request_url})\n\n"
    )

    # Send the message to both the default channel and the approvers
    logger.info(f"Approver message: {approver_message}")
    slack = get_slack_client()
    slack.send_message(message=approver_message)  # Sends to default channel
    for approver in approvers:
        slack.send_message(message=approver_message, user_email=approver.email)


@notification_hook_impl
def access_request_completed(
    access_request: AccessRequest,
    group: OktaGroup,
    requester: OktaUser,
    approvers: list[OktaUser],
    notify_requester: bool,
) -> None:
    """Notify the requester that their access request has been processed."""
    access_request_url = get_base_url() + f"/requests/{access_request.id}"

    requester_message = (
        f"Request for access to {group.name} has been {access_request.status.lower()}.\n\n"
        f"[View request]({access_request_url})"
    )

    # Send the message to both the default channel and the requester
    logger.info(f"Requester message: {requester_message}")
    slack = get_slack_client()
    slack.send_message(message=requester_message)  # Sends to default channel
    slack.send_message(message=requester_message, user_email=requester.email)


@notification_hook_impl
def access_expiring_user(groups: list[OktaGroup], user: OktaUser, expiration_datetime: datetime) -> None:
    """Notify individuals that their access to a group is expiring soon"""
    expiring_access_url = get_base_url() + "/expiring-groups?user_id=@me"

    group_or_groups = f"{len(groups)} groups" if len(groups) > 1 else f"the group {groups[0].name}"

    message = (
        f"Your access to {group_or_groups} is expiring {parse_dates(expiration_datetime, False)}.\n\n"
        f"Click [here]({expiring_access_url}) to view your access and, if still needed, create a request to renew it."
    )

    # Send the message to the individual user with expiring access
    logger.info(f"User message: {message}")
    slack = get_slack_client()
    slack.send_message(message=message, user_email=user.email)


@notification_hook_impl
def access_expiring_owner(
    owner: OktaUser,
    groups: list[OktaGroup],
    roles: list[OktaGroup],
    users: list[RoleGroup],
    expiration_datetime: datetime,
) -> None:
    """Notify group owners that individuals or roles access to a group is expiring soon"""
    if users is not None and len(users) > 0:
        expiring_access_url = get_base_url() + "/expiring-groups?owner_id=@me"

        single_or_group = "A member or owner" if len(users) == 1 else "Members or owners"
        group_or_groups = "a group" if len(groups) == 1 else "groups"

        message = (
            f"{single_or_group} of {group_or_groups} you own will lose access {parse_dates(expiration_datetime, True)}.\n\n"
            f"Click [here]({expiring_access_url}) to review the owners and members with expiring access and determine if the "
            f"access is still appropriate. If so, renew their membership/ownership so they do not lose access."
        )

        # Send the message to the group owner about the users with expiring access
        logger.info(f"Owner message: {message}")
        slack = get_slack_client()
        slack.send_message(message=message, user_email=owner.email)

    if roles is not None and len(roles) > 0:
        expiring_access_url = get_base_url() + "/expiring-roles?owner_id=@me"

        (single_or_group, is_are) = ("A role", "is") if len(roles) == 1 else ("Roles", "are")
        group_or_groups = "a group" if len(groups) == 1 else "groups"

        message = (
            f"{single_or_group} that {is_are} granted access to {group_or_groups} you own will lose access "
            f"{parse_dates(expiration_datetime, True)}.\n\n"
            f"Click [here]({expiring_access_url}) to view expiring roles and, if still appropriate, renew their access. "
        )

        # Send the message to the group owner about the roles with expiring access
        logger.info(f"Owner message: {message}")
        slack.send_message(message=message, user_email=owner.email)
