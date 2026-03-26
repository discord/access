from typing import Set

from api.models.app_group import get_access_owners, get_app_managers
from api.models.core_models import AccessRequest, AppGroup, GroupRequest, OktaUser, RoleRequest
from api.models.okta_group import get_group_managers


def get_all_possible_request_approvers(access_request: AccessRequest | RoleRequest | GroupRequest) -> Set[OktaUser]:
    # This will return the entire set of possible access request approvers
    # to ensure that even if the resolved set of approvers changes
    # we still are able to mark the request as resolved for any users
    # that were notified of the request.
    group_owners = []

    if type(access_request) is not GroupRequest:
        group_owners = get_group_managers(access_request.requested_group_id)

    access_app_owners = get_access_owners()

    app_managers = []

    if type(access_request) is not GroupRequest and type(access_request.requested_group) is AppGroup:
        app_managers = get_app_managers(access_request.requested_group.app_id)
    elif (
        type(access_request) is GroupRequest
        and access_request.requested_group_type == "app_group"
        and access_request.requested_app_id is not None
    ):
        app_managers = get_app_managers(access_request.requested_app_id)

    return set(group_owners + access_app_owners + app_managers)


def get_request_approvers(access_request: AccessRequest | RoleRequest | GroupRequest) -> Set[OktaUser]:
    # This will return the prioritized set of approvers for a request, following the
    # logic used when creating requests: group owners first, then app owners, then
    # access owners as a fallback. This avoids spamming possible approvers on
    # on request notifications.
    if type(access_request) is GroupRequest:
        app_id = access_request.resolved_app_id if access_request.resolved_app_id else access_request.requested_app_id
        if app_id is not None:
            approvers = get_app_managers(app_id)
            if len(approvers) == 0:
                approvers = get_access_owners()
        else:
            approvers = get_access_owners()
        return set(approvers)

    approvers = get_group_managers(access_request.requested_group_id)

    if (
        (len(approvers) == 0 and type(access_request.requested_group) is AppGroup)
        or (len(approvers) == 1 and approvers[0].id == access_request.requester_user_id)
        and type(access_request.requested_group) is AppGroup
    ):
        approvers = get_app_managers(access_request.requested_group.app_id)

    if len(approvers) == 0 or (len(approvers) == 1 and approvers[0].id == access_request.requester_user_id):
        approvers = get_access_owners()

    return set(approvers)
