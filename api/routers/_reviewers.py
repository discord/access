"""Builds the `/reviewers` response shared by the three request routers."""

from api.models.request_reviewers import AnyRequest, get_assigned_reviewers, get_possible_reviewers_by_level
from api.schemas import OktaUserSummary, RequestReviewerLevel, RequestReviewers


async def request_reviewers_response(request: AnyRequest) -> RequestReviewers:
    """The request's possible reviewers by level, with its assigned level marked."""
    assigned = await get_assigned_reviewers(request)
    levels = await get_possible_reviewers_by_level(request)
    return RequestReviewers(
        assigned_owner_level=assigned.owner_level,
        owner_levels=[
            RequestReviewerLevel(
                owner_level=level.owner_level,
                reviewers=[OktaUserSummary.model_validate(u, from_attributes=True) for u in level.reviewers],
            )
            for level in levels
        ],
    )
