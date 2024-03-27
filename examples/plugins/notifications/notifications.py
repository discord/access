from __future__ import print_function

import logging
import os
from datetime import date, datetime, timedelta

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser, RoleGroup

notification_hook_impl = pluggy.HookimplMarker('access_notifications')
logger = logging.getLogger(__name__)

def get_base_url() -> str:
    env = os.environ.get('FLASK_ENV', 'development')
    if env == 'production':
        return 'https://example.com'
    else:
        return 'http://localhost:3000'


def parse_dates(comparison_date: datetime, owner: bool) -> str:
    if not comparison_date:
        return 'soon'

    just_date = comparison_date.date()
    today = date.today()

    prev_monday = today - timedelta(days=today.weekday())

    if owner:  # 'round' dates to previous Monday
        if prev_monday <= just_date and just_date < prev_monday + timedelta(weeks=1):
            return 'this week'
        elif prev_monday + timedelta(weeks=1) <= just_date and just_date < prev_monday + timedelta(
            weeks=2
        ):
            return 'next week'
        elif prev_monday + timedelta(weeks=2) <= just_date and just_date < prev_monday + timedelta(
            weeks=3
        ):
            return 'in two weeks'
        elif prev_monday + timedelta(weeks=3) <= just_date and just_date < prev_monday + timedelta(
            weeks=4
        ):
            return 'in three weeks'
        elif prev_monday + timedelta(weeks=4) <= just_date and just_date < prev_monday + timedelta(
            weeks=5
        ):
            return 'in four weeks'
    else:
        if just_date == today + timedelta(days=1):  # one day before
            return 'tomorrow'
        elif just_date == today + timedelta(weeks=1):  # one week before
            return 'in a week'
        elif just_date == today + timedelta(weeks=2):  # 2 weeks before
            return 'in two weeks'

    return 'soon'


@notification_hook_impl
def access_request_created(access_request: AccessRequest,
                           group: OktaGroup,
                           requester: OktaUser,
                           approvers: list[OktaUser]) -> None:
    """Notify all the approvers of the access request through a notification"""

    type_of_access = 'ownership of' if access_request.request_ownership else 'membership to'

    access_request_url = get_base_url() + f'/requests/{access_request.id}'

    approver_message = (
        f'{requester.email} has requested {type_of_access} {group}.\n\n'
        f'[View request to approve or reject]({access_request_url})\n\n'
    )

    # Send the message to the approvers
    assert approver_message is not None


@notification_hook_impl
def access_request_completed(access_request: AccessRequest,
                             group: OktaGroup,
                             requester: OktaUser,
                             approvers: list[OktaUser],
                             notify_requester: bool) -> None:
    """Notify the requester that their access request has been processed."""
    access_request_url = get_base_url() + f'/requests/{access_request.id}'

    requester_message = (
        f'Request for access to {group} has been {access_request.status.lower()}.\n\n'
        f'[View request]({access_request_url})'
    )


    # Send the message to the requester and optionally update the approvers message
    assert requester_message is not None



@notification_hook_impl
def access_expiring_user(groups: list[OktaGroup],
                         user: OktaUser,
                         expiration_datetime: datetime) -> None:
    """Notify individuals that their access to a group is expiring soon"""
    expiring_access_url = get_base_url() + '/expiring-groups?user_id=@me'

    group_or_groups = f'{len(groups)} groups' if len(groups) > 1 else f'the group {groups[0].name}'

    message = (
        f'Your access to {group_or_groups} is expiring {parse_dates(expiration_datetime, False)}.\n\n'
        f'Click [here]({expiring_access_url}) to view your access and, if still needed, create a request to renew it.'
    )

    # Send the message to the individual user with expiring access
    assert message is not None

@notification_hook_impl
def access_expiring_owner(owner: OktaUser,
                          groups: list[OktaGroup],
                          roles: list[OktaGroup],
                          users: list[RoleGroup],
                          expiration_datetime: datetime) -> None:
    """Notify group owners that individuals or roles access to a group is expiring soon"""
    if users is not None and len(users) > 0:
        expiring_access_url = get_base_url() + '/expiring-groups?owner_id=@me'

        single_or_group = 'A member or owner' if len(users) == 1 else 'Members or owners'
        group_or_groups = 'a group' if len(groups) == 1 else 'groups'

        message = (
            f'{single_or_group} of {group_or_groups} you own will lose access {parse_dates(expiration_datetime, True)}.\n\n'
            f'Click [here]({expiring_access_url}) to review the owners and members with expiring access and determine if the '
            f'access is still appropriate. If so, renew their membership/ownership so they do not lose access.'
        )

        # Send the message to the group owner about the users with expiring access
        assert message is not None

    if roles is not None and len(roles) > 0:
        expiring_access_url = get_base_url() + '/expiring-roles?owner_id=@me'

        (single_or_group, is_are) = ('A role', 'is') if len(roles) == 1 else ('Roles', 'are')
        group_or_groups = 'a group' if len(groups) == 1 else 'groups'

        message = (
            f'{single_or_group} that {is_are} granted access to {group_or_groups} you own will lose access '
            f'{parse_dates(expiration_datetime, True)}.\n\n'
            f'Click [here]({expiring_access_url}) to view expiring roles and, if still appropriate, renew their access. '
        )

        # Send the message to the group owner about the roles with expiring access
        assert message is not None
