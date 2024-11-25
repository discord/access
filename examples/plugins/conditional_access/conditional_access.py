from __future__ import print_function

import logging
import os
from typing import List, Optional

import pluggy

from api.models import AccessRequest, OktaGroup, OktaUser, Tag
from api.plugins import ConditionalAccessResponse

request_hook_impl = pluggy.HookimplMarker("access_conditional_access")
logger = logging.getLogger(__name__)

# Constants for auto-approval conditions (not required if you only want to use the Auto-Approval TAG)
# Example of how to set this in an environment variable in your .env.production file:
# AUTO_APPROVED_GROUP_NAMES="Group1,Group2,Group3"
AUTO_APPROVED_GROUP_NAMES = (
    os.getenv("AUTO_APPROVED_GROUP_NAMES", "").split(",") if os.getenv("AUTO_APPROVED_GROUP_NAMES") else []
)

# Example of how to set this in an environment variable in your .env.production file:
# AUTO_APPROVED_TAG_NAMES="Tag1,Tag2,Tag3"
AUTO_APPROVED_TAG_NAMES = os.getenv("AUTO_APPROVED_TAG_NAMES", "Auto-Approve").split(",")


@request_hook_impl
def access_request_created(
    access_request: AccessRequest, group: OktaGroup, group_tags: List[Tag], requester: OktaUser
) -> Optional[ConditionalAccessResponse]:
    """Auto-approve memberships to the Auto-Approved-Group group"""

    if not access_request.request_ownership:
        # Check either group name or tag for auto-approval
        is_auto_approved_name = group.name in AUTO_APPROVED_GROUP_NAMES
        is_auto_approved_tag = any(tag.name in AUTO_APPROVED_TAG_NAMES for tag in group_tags)

        if is_auto_approved_name or is_auto_approved_tag:
            logger.info(f"Auto-approving access request {access_request.id} to group {group.name}")
            return ConditionalAccessResponse(
                approved=True, reason="Group membership auto-approved", ending_at=access_request.request_ending_at
            )

    logger.info(f"Access request {access_request.id} to group {group.name} requires manual approval")

    return None
