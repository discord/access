from __future__ import print_function

import logging
import os
from datetime import date, datetime, timedelta
from typing import List, Optional

import pluggy
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from api.models import AccessRequest, OktaGroup, OktaUser, RoleGroup

notification_hook_impl = pluggy.HookimplMarker("access_notifications")
logger = logging.getLogger(__name__)

# Initialize Slack client and signature verifier
slack_token = os.environ["SLACK_BOT_TOKEN"]
signing_secret = os.environ["SLACK_SIGNING_SECRET"]
client = WebClient(token=slack_token)
alerts_channel = os.environ.get("SLACK_ALERTS_CHANNEL")
CLIENT_ORIGIN_URL = os.environ.get("CLIENT_ORIGIN_URL")  # e.g. "https://discord-access-instance.com"


def get_base_url() -> str:
    """Get the base URL for the environment.

    Returns:
        str: The base URL based on the environment.
    """
    # Use CLIENT_ORIGIN_URL if defined; otherwise, determine based on the environment.
    if CLIENT_ORIGIN_URL:
        return CLIENT_ORIGIN_URL

    env = os.environ.get("FLASK_ENV", "development")
    if env == "production":
        return "https://example.com"
    else:
        return "http://localhost:3000"


def parse_dates(comparison_date: datetime, owner: bool) -> str:
    """Parse dates for notification messages.

    Args:
        comparison_date (datetime): The date to compare.
        owner (bool): Indicates if the user is an owner.

    Returns:
        str: The parsed date description.
    """
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


def get_user_id_by_email(email: str) -> Optional[str]:
    """Get Slack user ID by email.

    Args:
        email (str): The email of the user.

    Returns:
        Optional[str]: The Slack user ID if found, otherwise None.
    """
    try:
        response = client.users_lookupByEmail(email=email)
        return response["user"]["id"]
    except SlackApiError as e:
        logger.error(f"Error fetching user ID for {email}: {e.response['error']}")
        return None


def send_slack_dm(user: OktaUser, message: str) -> None:
    """Send a direct message to a Slack user.

    Args:
        user (OktaUser): The user to send the message to.
        message (str): The message content.
    """
    user_id = get_user_id_by_email(user.email)
    if user_id:
        mention_message = f"<@{user_id}> {message}"
        try:
            response = client.chat_postMessage(
                channel=user_id, text=mention_message, as_user=True, unfurl_links=True, unfurl_media=True
            )
            logger.info(f"Slack DM sent: {response['ts']}")
        except SlackApiError as e:
            logger.error(f"Error sending Slack message: {e.response['error']}")


def send_slack_channel_message(message: str) -> None:
    """Send a message to a Slack channel if the alerts_channel is defined.

    Args:
        message (str): The message content.
    """
    if alerts_channel:
        try:
            response = client.chat_postMessage(
                channel=alerts_channel, text=message, as_user=True, unfurl_links=True, unfurl_media=True
            )
            logger.info(f"Slack channel message sent: {response['ts']}")
        except SlackApiError as e:
            logger.error(f"Error sending Slack channel message: {e.response['error']}")


@notification_hook_impl
def access_request_created(
    access_request: AccessRequest, group: OktaGroup, requester: OktaUser, approvers: List[OktaUser]
) -> None:
    """Notify all the approvers of the access request through a notification.

    Args:
        access_request (AccessRequest): The access request.
        group (OktaGroup): The group for which access is requested.
        requester (OktaUser): The user requesting access.
        approvers (List[OktaUser]): The list of approvers.
    """
    type_of_access = "ownership of" if access_request.request_ownership else "membership to"

    access_request_url = get_base_url() + f"/requests/{access_request.id}"

    approver_message = (
        f":pray: {requester.email} has requested {type_of_access} {group.name}.\n\n"
        f"<{access_request_url}|View request to approve or reject>\n\n"
    )

    # Send the message to the approvers
    for approver in approvers:
        send_slack_dm(approver, approver_message)
    logger.info(f"Approver message: {approver_message}")

    # Post to the alerts channel
    send_slack_channel_message(approver_message)


@notification_hook_impl
def access_request_completed(
    access_request: AccessRequest,
    group: OktaGroup,
    requester: OktaUser,
    approvers: List[OktaUser],
    notify_requester: bool,
) -> None:
    """Notify the requester that their access request has been processed.

    Args:
        access_request (AccessRequest): The access request.
        group (OktaGroup): The group for which access is requested.
        requester (OktaUser): The user requesting access.
        approvers (List[OktaUser]): The list of approvers.
        notify_requester (bool): Whether to notify the requester.
    """
    access_request_url = get_base_url() + f"/requests/{access_request.id}"
    emoji = ":white_check_mark:" if access_request.status.lower() == "approved" else ":x:"

    requester_message = (
        f"{emoji} Request for access to {group.name} has been {access_request.status.lower()}.\n\n"
        f"<{access_request_url}|View request>\n"
    )

    # Send the message to the requester
    if notify_requester:
        send_slack_dm(requester, requester_message)
    logger.info(f"Requester message: {requester_message}")

    # Post to the alerts channel
    send_slack_channel_message(requester_message)


@notification_hook_impl
def access_expiring_user(groups: List[OktaGroup], user: OktaUser, expiration_datetime: datetime) -> None:
    """Notify individuals that their access to a group is expiring soon.

    Args:
        groups (List[OktaGroup]): The list of groups.
        user (OktaUser): The user whose access is expiring.
        expiration_datetime (datetime): The expiration date and time.
    """
    expiring_access_url = get_base_url() + "/expiring-groups?user_id=@me"

    group_or_groups = f"{len(groups)} groups" if len(groups) > 1 else f"the group {groups[0].name}"

    message = (
        f"Your access to {group_or_groups} is expiring {parse_dates(expiration_datetime, False)}.\n\n"
        f"Click <{expiring_access_url}|here> to view your access and, if still needed, create a request to renew it."
    )

    # Send the message to the individual user with expiring access
    send_slack_dm(user, message)
    logger.info(f"User message: {message}")

    # Post to the alerts channel
    send_slack_channel_message(message)


@notification_hook_impl
def access_expiring_owner(
    owner: OktaUser,
    groups: List[OktaGroup],
    roles: List[OktaGroup],
    users: List[RoleGroup],
    expiration_datetime: datetime,
) -> None:
    """Notify group owners that individuals or roles access to a group is expiring soon.

    Args:
        owner (OktaUser): The owner of the group.
        groups (List[OktaGroup]): The list of groups.
        roles (List[OktaGroup]): The list of roles.
        users (List[RoleGroup]): The list of users.
        expiration_datetime (datetime): The expiration date and time.
    """
    if users is not None and len(users) > 0:
        expiring_access_url = get_base_url() + "/expiring-groups?owner_id=@me"

        single_or_group = "A member or owner" if len(users) == 1 else "Members or owners"
        group_or_groups = "a group" if len(groups) == 1 else "groups"

        message = (
            f"{single_or_group} of {group_or_groups} you own will lose access {parse_dates(expiration_datetime, True)}.\n\n"
            f"Click <{expiring_access_url}|here> to review the owners and members with expiring access and determine if the "
            f"access is still appropriate. If so, renew their membership/ownership so they do not lose access."
        )

        # Send the message to the group owner about the users with expiring access
        send_slack_dm(owner, message)
        logger.info(f"Owner message: {message}")

        # Post to the alerts channel
        send_slack_channel_message(message)

    if roles is not None and len(roles) > 0:
        expiring_access_url = get_base_url() + "/expiring-roles?owner_id=@me"

        (single_or_group, is_are) = ("A role", "is") if len(roles) == 1 else ("Roles", "are")
        group_or_groups = "a group" if len(groups) == 1 else "groups"

        message = (
            f"{single_or_group} that {is_are} granted access to {group_or_groups} you own will lose access "
            f"{parse_dates(expiration_datetime, True)}.\n\n"
            f"Click <{expiring_access_url}|here> to view expiring roles and, if still appropriate, renew their access."
        )

        # Send the message to the group owner about the roles with expiring access
        send_slack_dm(owner, message)
        logger.info(f"Owner message: {message}")

        # Post to the alerts channel
        send_slack_channel_message(message)
