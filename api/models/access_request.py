from typing import List, Set

from api.models.app_group import get_access_owners, get_app_managers
from api.models.core_models import AccessRequest, AppGroup, GroupRequest, OktaUser, RoleRequest
from api.models.okta_group import get_group_managers


def get_all_possible_request_approvers(access_request: AccessRequest | RoleRequest | GroupRequest) -> Set[OktaUser]:
    # This will return the entire set of possible access request approvers
    # to ensure that even if the resolved set of approvers changes
    # we still are able to mark the request as resolved for any users
    # that were notified of the request.
    group_owners: List[OktaUser] = []
    app_managers: List[OktaUser] = []

    # AccessRequest / RoleRequest have `requested_group_id` + `requested_group`;
    # GroupRequest doesn't (it tracks a *requested* group as separate name/type
    # fields, since the group hasn't been created yet). Branch on the type so
    # mypy narrows the union accurately.
    if isinstance(access_request, (AccessRequest, RoleRequest)):
        group_owners = get_group_managers(access_request.requested_group_id)
        if isinstance(access_request.requested_group, AppGroup):
            app_managers = get_app_managers(access_request.requested_group.app_id)
    elif (
        isinstance(access_request, GroupRequest)
        and access_request.requested_group_type == "app_group"
        and access_request.requested_app_id is not None
    ):
        app_managers = get_app_managers(access_request.requested_app_id)

    access_app_owners = get_access_owners()

    return set(group_owners + access_app_owners + app_managers)
