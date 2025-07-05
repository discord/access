from __future__ import print_function

import logging
import os
import random
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, TypeVar

import pluggy
from slack_sdk import WebClient

from api.models import AccessRequest, OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, RoleRequest

notification_hook_impl = pluggy.HookimplMarker("access_notifications")
logger = logging.getLogger(__name__)

# Type variable for generic return type
T = TypeVar("T")


def retry_operation(
    operation_func: Callable[[], T], max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 10.0
) -> Optional[T]:
    """
    Execute an operation with retries, without propagating exceptions

    Args:
        operation_func: Function that performs the operation
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds

    Returns:
        The result of the operation or None if all attempts fail
    """
    attempt = 0
    delay = base_delay

    while attempt < max_attempts:
        try:
            return operation_func()
        except Exception as e:
            attempt += 1

            # Log differently based on attempt number
            if attempt >= max_attempts:
                logger.error(f"Max retry attempts ({max_attempts}) reached. Last error: {str(e)}")
                return None
            else:
                # Add jitter to avoid thundering herd
                jitter = random.uniform(0, 0.1 * delay)
                sleep_time = min(delay + jitter, max_delay)
                logger.warning(
                    f"Operation failed (attempt {attempt}/{max_attempts}): {str(e)}. Retrying in {sleep_time:.2f}s"
                )
                time.sleep(sleep_time)
                delay = min(delay * 2, max_delay)  # Exponential backoff

    return None


# Initialize Slack client and signature verifier
slack_token = os.environ["SLACK_BOT_TOKEN"]
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


def expiring_access_list_user(expiring_access_list: List[OktaUserGroupMember] | None) -> str:
    """Formats list of expiring access for expiring access individual DMs.

    Args:
        expiring_access_list (List[OktaUserGroupMember] | None): User access expiring soon.

    Returns:
        str: Formatted string of user access expiring soon.
    """
    if not expiring_access_list:
        return ""

    num_expiring = len(expiring_access_list)

    out = ""
    for i in range(min(10, num_expiring)):
        member_owner = "Ownership of" if expiring_access_list[i].is_owner else "Membership to"
        out = out + f"- {member_owner} {expiring_access_list[i].group.name}\n"
    if num_expiring > 10:
        out = out + "and more...\n"

    return out


def expiring_access_list_owner_roles(expiring_access_list: List[RoleGroupMap] | None) -> str:
    """Formats list of expiring role access for expiring access group owner DMs.

    Args:
        expiring_access_list (List[RoleGroupMap] | None): Role access expiring soon.

    Returns:
        str: Formatted string of role access expiring soon.
    """
    if not expiring_access_list:
        return ""

    num_expiring = len(expiring_access_list)

    out = ""
    for i in range(min(10, num_expiring)):
        member_owner = "ownership of" if expiring_access_list[i].is_owner else "membership to"
        out = (
            out + f"- {expiring_access_list[i].role_group.name}'s {member_owner} {expiring_access_list[i].group.name}\n"
        )
    if num_expiring > 10:
        out = out + "and more...\n"

    return out


def expiring_access_list_owner_users(expiring_access_list: List[OktaUserGroupMember] | None) -> str:
    """Formats list of expiring user access for expiring access group owner DMs.

    Args:
        expiring_access_list (List[OktaUserGroupMember] | None): User access expiring soon.

    Returns:
        str: Formatted string of user access expiring soon.
    """
    if not expiring_access_list:
        return ""

    num_expiring = len(expiring_access_list)

    out = ""
    for i in range(min(10, num_expiring)):
        user_name = (
            expiring_access_list[i].user.display_name
            if expiring_access_list[i].user.display_name is not None
            else expiring_access_list[i].user.first_name + " " + expiring_access_list[i].user.last_name
        )
        member_owner = "ownership of" if expiring_access_list[i].is_owner else "membership to"
        out = out + f"- {user_name}'s {member_owner} {expiring_access_list[i].group.name}\n"
    if num_expiring > 10:
        out = out + "and more...\n"

    return out


def expiring_access_list_role_owner(expiring_access_list: List[RoleGroupMap]) -> str:
    """Formats list of expiring role access for expiring access role owner DMs.

    Args:
        expiring_access_list (List[RoleGroupMap] | None): Role access expiring soon.

    Returns:
        str: Formatted string of role access expiring soon.
    """
    if not expiring_access_list:
        return ""

    num_expiring = len(expiring_access_list)

    out = ""
    for i in range(min(10, num_expiring)):
        member_owner = "ownership of" if expiring_access_list[i].is_owner else "membership to"
        out = (
            out + f"- {expiring_access_list[i].role_group.name}'s {member_owner} {expiring_access_list[i].group.name}\n"
        )
    if num_expiring > 10:
        out = out + "and more...\n"

    return out


def get_user_id_by_email(email: str) -> Optional[str]:
    """Get Slack user ID by email with retry logic.

    Args:
        email (str): The email of the user.

    Returns:
        Optional[str]: The Slack user ID if found, otherwise None.
    """

    def lookup_user() -> str:
        response = client.users_lookupByEmail(email=email)
        return response["user"]["id"]

    user_id = retry_operation(lookup_user)
    if not user_id:
        logger.error(f"Failed to fetch user ID for {email} after multiple attempts")

    return user_id


def send_slack_dm(user: OktaUser, message: str) -> None:
    """Send a direct message to a Slack user with retry logic.

    Args:
        user (OktaUser): The user to send the message to.
        message (str): The message content.
    """
    user_id = get_user_id_by_email(user.email)
    if user_id:
        mention_message = f"<@{user_id}> {message}"

        def send_message() -> Dict[str, Any]:
            response = client.chat_postMessage(
                channel=user_id, text=mention_message, as_user=True, unfurl_links=True, unfurl_media=True
            )
            logger.info(f"Slack DM sent: {response['ts']}")
            return response

        result = retry_operation(send_message)
        if not result:
            logger.error(f"Failed to send Slack DM to {user.email} after multiple attempts")


def send_slack_channel_message(user: OktaUser, message: str) -> None:
    """Send a message to a Slack channel with retry logic.

    Args:
        message (str): The message content.
        user (OktaUser): The user to relate the message to.
    """
    if alerts_channel:
        user_id = get_user_id_by_email(user.email)

        if user_id:
            channel_message = f"{user.email} - {message}"

            def send_message() -> Dict[str, Any]:
                response = client.chat_postMessage(
                    channel=alerts_channel, text=channel_message, as_user=True, unfurl_links=True, unfurl_media=True
                )
                logger.info(f"Slack channel message sent: {response['ts']}")
                return response

            result = retry_operation(send_message)
            if not result:
                logger.error(f"Failed to send message to channel {alerts_channel} after multiple attempts")


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
    type_of_access = (
        "*ownership* :large_yellow_circle: of" if access_request.request_ownership else "*membership* :white_circle: to"
    )

    access_request_url = get_base_url() + f"/requests/{access_request.id}"

    approver_message = (
        f":pray: {requester.email} has requested {type_of_access} {group.name}.\n\n"
        f"<{access_request_url}|View request to approve or reject>\n\n"
    )

    # Send the message to the approvers
    for approver in approvers:
        send_slack_dm(approver, approver_message)
    logger.info(f"Approver message: {approver_message}")

    # Send the message to the requester only if they're not already an approver
    if requester.id not in [approver.id for approver in approvers]:
        send_slack_dm(requester, approver_message)
        logger.info("Requester received creation notification")

    # Post to the alerts channel
    send_slack_channel_message(requester, approver_message)


@notification_hook_impl
def access_role_request_created(
    role_request: RoleRequest, role: RoleGroup, group: OktaGroup, requester: OktaUser, approvers: List[OktaUser]
) -> None:
    """Notify all the approvers of the role request through a notification.

    Args:
        role_request (AccessRequest): The access request.
        role (RoleGroup): The role for which access is requested.
        group (OktaGroup): The group to which access is requested.
        requester (OktaUser): The user requesting access.
        approvers (List[OktaUser]): The list of approvers.
    """
    type_of_access = "ownership of" if role_request.request_ownership else "membership to"

    role_request_url = get_base_url() + f"/requests/{role_request.id}"

    approver_message = (
        f":pray: {requester.email} has requested that {role.name} is granted {type_of_access} {group.name}.\n\n"
        f"<{role_request_url}|View request to approve or reject>\n\n"
    )

    # Send the message to the approvers
    for approver in approvers:
        send_slack_dm(approver, approver_message)
    logger.info(f"Approver message: {approver_message}")

    # Send the message to the requester only if they're not already an approver
    if requester.id not in [approver.id for approver in approvers]:
        send_slack_dm(requester, approver_message)
        logger.info("Requester received creation notification")

    # Post to the alerts channel
    send_slack_channel_message(requester, approver_message)


@notification_hook_impl
def access_request_completed(
    access_request: AccessRequest,
    group: OktaGroup,
    requester: OktaUser,
    approvers: List[OktaUser],
) -> None:
    """Notify the requester that their access request has been processed.

    Args:
        access_request (AccessRequest): The access request.
        group (OktaGroup): The group for which access is requested.
        requester (OktaUser): The user requesting access.
        approvers (List[OktaUser]): The list of approvers.
    """
    access_request_url = get_base_url() + f"/requests/{access_request.id}"
    emoji = ":white_check_mark:" if access_request.status.lower() == "approved" else ":x:"

    requester_message = (
        f"{emoji} Request for access to {group.name} has been {access_request.status.lower()}.\n\n"
        f"<{access_request_url}|View request>\n"
    )

    # Send the message to the requester
    send_slack_dm(requester, requester_message)
    logger.info(f"Requester message: {requester_message}")

    # Send the message to all approvers (except the requester)
    for approver in approvers:
        if approver.id != requester.id:  # Skip if approver is the requester
            send_slack_dm(approver, requester_message)
    logger.info("Approvers received completion notification")

    # Post to the alerts channel
    send_slack_channel_message(requester, requester_message)


@notification_hook_impl
def access_expiring_user(
    groups: List[OktaGroup],
    user: OktaUser,
    expiration_datetime: datetime,
    okta_user_group_members: List[OktaUserGroupMember],
) -> None:
    """Notify individuals that their access to a group is expiring soon.

    Args:
        groups (List[OktaGroup]): The list of groups.
        user (OktaUser): The user whose access is expiring.
        expiration_datetime (datetime): The expiration date and time.
        okta_user_group_members (List[OktaUserGroupMember]): List of expiring memberships and ownerships.
    """
    expiring_access_url = get_base_url() + "/expiring-groups?user_id=@me"

    expiring_access_list = expiring_access_list_user(okta_user_group_members)
    count_of_expirations = len(okta_user_group_members)

    message = (
        f"You have access to {count_of_expirations} groups that will be expiring {parse_dates(expiration_datetime, False)}\n\n"
        f"{expiring_access_list}.\n"
        f"<{expiring_access_url}|View your access and, if needed, create a request to renew it.>"
    )

    # Send the message to the individual user with expiring access
    send_slack_dm(user, message)
    logger.info(f"User message: {message}")

    # Post to the alerts channel
    send_slack_channel_message(user, message)


@notification_hook_impl
def access_expiring_owner(
    owner: OktaUser,
    groups: List[OktaGroup],
    roles: List[RoleGroup],
    users: List[OktaUser],
    expiration_datetime: datetime,
    group_user_associations: Optional[List[OktaUserGroupMember]],
    role_group_associations: Optional[List[RoleGroupMap]],
) -> None:
    """Notify group owners that individuals or roles access to a group is expiring soon.

    Args:
        owner (OktaUser): The owner of the group.
        groups (List[OktaGroup]): The list of groups.
        roles (List[OktaGroup]): The list of roles.
        users (List[RoleGroup]): The list of users.
        expiration_datetime (datetime): The expiration date and time.
        group_user_associations (Optional[List[OktaUserGroupMember]]): List of memberships and ownerships expiring.
        role_group_associations (Optional[List[RoleGroupMap]]): List of role memberships and ownerships expiring.
    """
    if group_user_associations is not None and len(group_user_associations) > 0:
        expiring_access_url = get_base_url() + "/expiring-groups?owner_id=@me"

        num_users = len(group_user_associations)

        (user_or_users, is_are) = ("A user", "is") if num_users == 1 else (str(num_users) + " users", "are")
        group_or_groups = "a group" if len(group_user_associations) == 1 else "groups"
        expiring_access_list = expiring_access_list_owner_users(group_user_associations)

        message = (
            f"{user_or_users} that {is_are} granted access to {group_or_groups} you own will lose access "
            f"{parse_dates(expiration_datetime, True)}\n\n"
            f"{expiring_access_list}\n"
            f"Please <{expiring_access_url}|review expiring individual access> to decide whether it should be ended or renewed.\n\n"
        )

        # Send the message to the group owner about the users with expiring access
        send_slack_dm(owner, message)
        logger.info(f"Owner message: {message}")

        # Post to the alerts channel
        send_slack_channel_message(owner, message)

    if role_group_associations is not None and len(role_group_associations) > 0:
        expiring_access_url = get_base_url() + "/expiring-roles?owner_id=@me"

        num_roles = len(role_group_associations)

        (role_or_roles, is_are) = ("A role", "is") if num_roles == 1 else (str(num_roles) + " roles", "are")
        group_or_groups = "a group" if len(role_group_associations) == 1 else "groups"
        expiring_access_list = expiring_access_list_owner_roles(role_group_associations)

        message = (
            f"{role_or_roles} that {is_are} granted access to {group_or_groups} you own will lose access "
            f"{parse_dates(expiration_datetime, True)}\n\n"
            f"{expiring_access_list}\n"
            f"Please <{expiring_access_url}|review expiring role-based access> to decide whether it should be ended or renewed.\n\n"
        )

        # Send the message to the group owner about the roles with expiring access
        send_slack_dm(owner, message)
        logger.info(f"Owner message: {message}")

        # Post to the alerts channel
        send_slack_channel_message(owner, message)


@notification_hook_impl
def access_expiring_role_owner(owner: OktaUser, roles: List[RoleGroupMap], expiration_datetime: datetime) -> None:
    """Notify role owners that roles they own will be losing access to groups soon.

    Args:
        owner (OktaUser): The owner of the group.
        roles (roles: List[RoleGroupMap]): List of role memberships and ownerships expiring.
        expiration_datetime (datetime): The expiration date and time.
    """
    expiring_access_url = get_base_url() + "/expiring-roles?role_owner_id=@me"

    expiring_access_list = expiring_access_list_role_owner(roles)
    count_of_expirations = len(roles)
    role_or_roles = "A role" if count_of_expirations == 1 else str(count_of_expirations) + " roles"

    message = (
        f"{role_or_roles} that you own will be losing access {parse_dates(expiration_datetime, False)}\n\n"
        f"{expiring_access_list}.\n"
        f"<{expiring_access_url}|View expiring access and, if needed, create a request to renew it.>)"
    )

    # Send the message to the group owner about the roles with expiring access
    send_slack_dm(owner, message)
    logger.info(f"Role owner message: {message}")

    # Post to the alerts channel
    send_slack_channel_message(owner, message)
