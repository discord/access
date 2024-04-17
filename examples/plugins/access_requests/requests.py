from __future__ import print_function

import logging
from typing import Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser
from api.plugins import ConditionalAccessResponse

request_hook_impl = pluggy.HookimplMarker('access_requests')
logger = logging.getLogger(__name__)

@request_hook_impl
def access_request_created(access_request: AccessRequest,
                           group: OktaGroup,
                           requester: OktaUser) -> Optional[ConditionalAccessResponse]:
    """Auto-approve memberships to the Auto-Approved-Group group"""

    if not access_request.request_ownership and group.name == "Auto-Approved-Group":
        return ConditionalAccessResponse(
            approved=True,
            reason="Group membership auto-approved",
            ending_at=access_request.request_ending_at
        )

    return None