"""The pending requests a group's deletion or unmanaging closes.

`DeleteGroup` and `UnmanageGroup` select these twice: once before they change
the group's ownership, for `snapshot_assigned_reviewers`, and again when
rejecting. Both selections go through here so the snapshot covers exactly the
requests that are later rejected.
"""

from typing import Sequence

from sqlalchemy import or_, select

from api.extensions import db
from api.models import AccessRequest, AccessRequestStatus, OktaUser, RoleRequest
from api.models.request_reviewers import snapshot_assigned_reviewers


async def pending_access_requests_for_group(group_id: str) -> Sequence[AccessRequest]:
    """Return the pending access requests for the group `group_id`."""
    return (
        await db.session.scalars(
            select(AccessRequest)
            .where(AccessRequest.requested_group_id == group_id)
            .where(AccessRequest.status == AccessRequestStatus.PENDING)
            .where(AccessRequest.resolved_at.is_(None))
        )
    ).all()


async def pending_role_requests_for_group(group_id: str) -> Sequence[RoleRequest]:
    """Return the pending role requests naming `group_id` as the requested group or the requesting role."""
    return (
        await db.session.scalars(
            select(RoleRequest)
            .where(or_(RoleRequest.requested_group_id == group_id, RoleRequest.requester_role_id == group_id))
            .where(RoleRequest.status == AccessRequestStatus.PENDING)
            .where(RoleRequest.resolved_at.is_(None))
        )
    ).all()


async def snapshot_obsolete_request_reviewers(
    group_id: str,
) -> tuple[dict[str, list[OktaUser]], dict[str, list[OktaUser]]]:
    """Return the assigned reviewers of the group's pending access and role requests, keyed by request id.

    Args:
        group_id: The group about to be deleted or unmanaged.

    Returns:
        A pair of request-id-to-reviewers maps: access requests, then role requests.
    """
    access_reviewers = await snapshot_assigned_reviewers(await pending_access_requests_for_group(group_id))
    role_reviewers = await snapshot_assigned_reviewers(await pending_role_requests_for_group(group_id))
    return access_reviewers, role_reviewers
