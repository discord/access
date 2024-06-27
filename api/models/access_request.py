from typing import Set

from api.models.app_group import get_access_owners, get_app_managers
from api.models.core_models import AccessRequest, AppGroup, OktaUser
from api.models.okta_group import get_group_managers


def get_all_possible_request_approvers(access_request: AccessRequest) -> Set[OktaUser]:
    # This will return the entire set of possible access request approvers
    # to ensure that even if the resolved set of approvers changes
    # we still are able to mark the request as resolved for any users
    # that were notified of the request.
    group_owners = get_group_managers(access_request.requested_group_id)
    access_app_owners = get_access_owners()

    app_managers = []

    if type(access_request.requested_group) is AppGroup:
        app_managers = get_app_managers(access_request.requested_group.app_id)

    return set(group_owners + access_app_owners + app_managers)
