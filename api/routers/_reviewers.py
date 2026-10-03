"""Builds the `/reviewers` response shared by the three request routers."""

from api.models.request_reviewers import AnyRequest, get_eligible_reviewers_by_level
from api.schemas import OktaUserSummary, RequestReviewerLevel, RequestReviewers


async def request_reviewers_response(request: AnyRequest) -> RequestReviewers:
    """The request's eligible reviewers by level, nearest first; see `RequestReviewers`."""
    levels = await get_eligible_reviewers_by_level(request)
    return RequestReviewers(
        reviewers_by_level=[
            RequestReviewerLevel(
                owner_level=level.owner_level,
                reviewers=[OktaUserSummary.model_validate(u, from_attributes=True) for u in level.reviewers],
            )
            for level in levels
        ],
    )
