from __future__ import print_function

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup, RoleGroupMap, RoleRequest

notification_hook_impl = pluggy.HookimplMarker("access_notifications")
logger = logging.getLogger(__name__)


def get_base_url() -> str:
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


def expiring_access_list_user(expiring_access_list: list[OktaUserGroupMember] | None) -> str:
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


def expiring_access_list_owner_roles(expiring_access_list: list[RoleGroupMap] | None) -> str:
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


def expiring_access_list_owner_users(expiring_access_list: list[OktaUserGroupMember] | None) -> str:
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


def expiring_access_list_role_owner(expiring_access_list: list[RoleGroupMap]) -> str:
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

    # Send the message to the approvers
    logger.info(f"Approver message: {approver_message}")


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

    # Send the message to the requester and optionally update the approvers message
    logger.info(f"Requester message: {requester_message}")


@notification_hook_impl
def access_role_request_created(
    role_request: RoleRequest, role: RoleGroup, group: OktaGroup, requester: OktaUser, approvers: list[OktaUser]
) -> None:
    """Notify all the approvers of the role request through a direct message."""
    type_of_access = "ownership of" if role_request.request_ownership else "membership to"

    role_request_url = get_base_url() + f"/requests/{role_request.id}"

    approver_message = (
        f"{requester.email} has requested that {role.name} is granted {type_of_access} {group.name}.\n\n"
        f"[View request to approve or reject]({role_request_url})\n\n"
    )

    # Send the message to the approvers
    logger.info(f"Approver message: {approver_message}")


@notification_hook_impl
def access_expiring_user(
    groups: list[OktaGroup],
    user: OktaUser,
    expiration_datetime: datetime,
    okta_user_group_members: list[OktaUserGroupMember],
) -> None:
    expiring_access_url = get_base_url() + "/expiring-groups?user_id=@me"

    expiring_access_list = expiring_access_list_user(okta_user_group_members)
    count_of_expirations = len(okta_user_group_members)

    message = (
        f"You have access to {count_of_expirations} groups that will be expiring {parse_dates(expiration_datetime, False)}\n\n"
        f"{expiring_access_list}.\n"
        f"[View your access and, if needed, create a request to renew it.]({expiring_access_url})"
    )

    # Send the message to the individual user with expiring access
    logger.info(f"User message: {message}")


@notification_hook_impl
def access_expiring_owner(
    owner: OktaUser,
    groups: list[OktaGroup],
    roles: list[RoleGroup],
    users: list[OktaUser],
    expiration_datetime: datetime,
    group_user_associations: Optional[list[OktaUserGroupMember]],
    role_group_associations: Optional[list[RoleGroupMap]],
) -> None:
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
            f"Please [review expiring individual access]({expiring_access_url}) to decide whether it should be ended or renewed.\n\n"
        )

        # Send the message to the group owner about the users with expiring access
        logger.info(f"Owner message: {message}")

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
            f"Please [review expiring role-based access]({expiring_access_url}) to decide whether it should be ended or renewed.\n\n"
        )

        # Send the message to the group owner about the roles with expiring access
        logger.info(f"Owner message: {message}")


@notification_hook_impl
def access_expiring_role_owner(owner: OktaUser, roles: list[RoleGroupMap], expiration_datetime: datetime) -> None:
    expiring_access_url = get_base_url() + "/expiring-roles?role_owner_id=@me"

    expiring_access_list = expiring_access_list_role_owner(roles)
    count_of_expirations = len(roles)
    role_or_roles = "A role" if count_of_expirations == 1 else str(count_of_expirations) + " roles"

    message = (
        f"{role_or_roles} that you own will be losing access {parse_dates(expiration_datetime, False)}\n\n"
        f"{expiring_access_list}.\n"
        f"[View expiring access and, if needed, create a request to renew it.]({expiring_access_url})"
    )

    # Send the message to the role owner about the roles with expiring access
    logger.info(f"Owner message: {message}")
