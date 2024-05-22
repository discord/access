from __future__ import print_function

import logging
from typing import List, Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser, Tag
from api.plugins import ConditionalAccessResponse

request_hook_impl = pluggy.HookimplMarker("access_conditional_access")
logger = logging.getLogger(__name__)


@request_hook_impl
def access_request_created(
    access_request: AccessRequest, group: OktaGroup, group_tags: List[Tag], requester: OktaUser
) -> Optional[ConditionalAccessResponse]:
    """Auto-approve memberships to the Auto-Approved-Group group"""

    if not access_request.request_ownership and group.name == "Auto-Approved-Group":
        logger.info(f"Auto-approving access request {access_request.id} to group {group.name}")
        return ConditionalAccessResponse(
            approved=True, reason="Group membership auto-approved", ending_at=access_request.request_ending_at
        )

    logger.info(f"Access request {access_request.id} to group {group.name} requires manual approval")

    return None
