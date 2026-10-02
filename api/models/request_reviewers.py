"""Who reviews a request.

This module is the one definition of request reviewers. Notifications, the
"Assigned to Me" views, and the request detail pages all read it.

Each request has a list of owner levels, nearest first:

- An access request (`AccessRequest`) or role request (`RoleRequest`) for
  group G: the owners of G; then the owners of G's app, if G is an app
  group; then Access admins.
- A group request (`GroupRequest`) to create G: the owners of the requested
  app, if G is an app group; then Access admins.

A user who owns at a level is an eligible reviewer, with three exceptions.
The requester is not eligible. A deleted user is not eligible. And a user
who is not an Access admin is not eligible when approving would add them to
G against an enabled `disallow_self_add_*` tag on G. Only a role request can
do that, when the reviewer is a member of the requesting role. This matches
`CheckForSelfAdd.execute_for_role`, which blocks that approval.

A request's assigned reviewers are the eligible reviewers at the nearest
level that has any. Each level is a SQL condition on the request row. So one
definition answers both "who reviews this request"
(`get_eligible_reviewers_by_level`, `get_assigned_reviewers`) and "which
requests does this user review" (`is_assigned_reviewer`).

Being assigned does not decide who may resolve a request. The approve and
reject routes check that on their own.
"""

from dataclasses import dataclass
from typing import Any, Callable, Literal, cast

from sqlalchemy import ColumnElement, ColumnExpressionArgument, and_, exists, func, not_, or_, select
from sqlalchemy.orm import aliased

from api.extensions import db
from api.models.core_models import (
    AccessRequest,
    App,
    AppGroup,
    GroupRequest,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleRequest,
    Tag,
)

OwnerLevel = Literal["group_owners", "app_owners", "access_admins"]
AnyRequest = AccessRequest | RoleRequest | GroupRequest
RequestModel = type[AccessRequest] | type[RoleRequest] | type[GroupRequest]
ReviewerId = ColumnExpressionArgument[str] | str
_OwnershipCondition = Callable[[RequestModel, ReviewerId], ColumnElement[bool]]


@dataclass(frozen=True)
class OwnerLevelReviewers:
    """A request's eligible reviewers at one owner level.

    Attributes:
        owner_level: The level.
        reviewers: The users eligible at this level and at no nearer one.
    """

    owner_level: OwnerLevel
    reviewers: list[OktaUser]


def _is_active(row: type[OktaUserGroupMember] | type[OktaGroupTagMap]) -> ColumnElement[bool]:
    """The row has not ended."""
    return or_(row.ended_at.is_(None), row.ended_at > func.now())


def _exists(froms: tuple[Any, ...], *conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    """EXISTS over `froms`, correlated to every other table its conditions name.

    SQLAlchemy only correlates a subquery to the query directly around it. A
    nested EXISTS that names the request row or the reviewer column from
    further out would get its own unrelated copy of that table. Listing
    `froms` also renders a flat-aliased `AppGroup` as `okta_group JOIN
    app_group`, not as two unjoined tables.
    """
    return exists().select_from(*froms).where(*conditions).correlate_except(*froms)


def _owns_group(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns the requested group of an access or role request."""
    assert request_cls is not GroupRequest
    cls = cast(type[AccessRequest] | type[RoleRequest], request_cls)
    group = aliased(OktaGroup)
    ownership = aliased(OktaUserGroupMember)
    return _exists(
        (group, ownership),
        group.id == cls.requested_group_id,
        group.deleted_at.is_(None),
        ownership.group_id == group.id,
        ownership.user_id == reviewer_id,
        ownership.is_owner.is_(True),
        _is_active(ownership),
    )


def _owns_app(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns the owners group of the request's app.

    For a group request, the app is the requested app, and only when the
    request is for an app group. For an access or role request, the app is
    the one the requested app group belongs to.

    Each branch is one flat EXISTS that joins the requested group (if any),
    the owners group, and the ownership row. Postgres can run a flat EXISTS
    as a semi-join. An EXISTS nested inside it would make Postgres rescan
    users for every request, and an admin's "Assigned to Me" would take tens
    of seconds.

    The app's `deleted_at` is not checked, because `DeleteApp` relies on it.
    `DeleteApp` marks the app deleted first, then deletes its groups with the
    owners group last. Until then, requests for its other groups still go to
    the app owners.
    """
    owners_group = aliased(AppGroup, flat=True)
    ownership = aliased(OktaUserGroupMember)
    owns_owners_group = [
        owners_group.is_owner.is_(True),
        owners_group.deleted_at.is_(None),
        ownership.group_id == owners_group.id,
        ownership.user_id == reviewer_id,
        ownership.is_owner.is_(True),
        _is_active(ownership),
    ]
    if request_cls is GroupRequest:
        return and_(
            GroupRequest.requested_group_type == "app_group",
            _exists(
                (owners_group, ownership),
                owners_group.app_id == GroupRequest.requested_app_id,
                *owns_owners_group,
            ),
        )
    cls = cast(type[AccessRequest] | type[RoleRequest], request_cls)
    group = aliased(AppGroup, flat=True)
    return _exists(
        (group, owners_group, ownership),
        group.id == cls.requested_group_id,
        group.deleted_at.is_(None),
        owners_group.app_id == group.app_id,
        *owns_owners_group,
    )


def _is_access_admin(_request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer is a member of the Access app's owners group, as `is_access_admin` checks."""
    access_app = aliased(App)
    owners_group = aliased(AppGroup, flat=True)
    membership = aliased(OktaUserGroupMember)
    return _exists(
        (access_app, owners_group, membership),
        access_app.name == App.ACCESS_APP_RESERVED_NAME,
        access_app.deleted_at.is_(None),
        owners_group.app_id == access_app.id,
        owners_group.is_owner.is_(True),
        owners_group.deleted_at.is_(None),
        membership.group_id == owners_group.id,
        membership.user_id == reviewer_id,
        membership.is_owner.is_(False),
        _is_active(membership),
    )


def _owner_levels(request_cls: RequestModel) -> list[tuple[OwnerLevel, _OwnershipCondition]]:
    """The owner levels for a kind of request, nearest first."""
    if request_cls is GroupRequest:
        return [("app_owners", _owns_app), ("access_admins", _is_access_admin)]
    return [
        ("group_owners", _owns_group),
        ("app_owners", _owns_app),
        ("access_admins", _is_access_admin),
    ]


def _is_eligible(
    request_cls: RequestModel, ownership_condition: _OwnershipCondition, reviewer_id: ReviewerId
) -> ColumnElement[bool]:
    """The reviewer owns at this level and is eligible to review the request."""
    reviewer = aliased(OktaUser)
    conditions = [
        ownership_condition(request_cls, reviewer_id),
        request_cls.requester_user_id != reviewer_id,
        _exists((reviewer,), reviewer.id == reviewer_id, reviewer.deleted_at.is_(None)),
    ]
    if request_cls is RoleRequest:
        # Not blocked by a `disallow_self_add_*` tag: approving would add a
        # non-admin reviewer who is a member of the requesting role to the group.
        role_membership = aliased(OktaUserGroupMember)
        group = aliased(OktaGroup)
        tag_map = aliased(OktaGroupTagMap)
        tag = aliased(Tag)
        blocked_by_tag = and_(
            not_(_is_access_admin(request_cls, reviewer_id)),
            _exists(
                (role_membership,),
                role_membership.group_id == RoleRequest.requester_role_id,
                role_membership.user_id == reviewer_id,
                role_membership.is_owner.is_(False),
                _is_active(role_membership),
            ),
            _exists(
                (group, tag_map, tag),
                group.id == RoleRequest.requested_group_id,
                group.is_managed.is_(True),
                tag_map.group_id == group.id,
                _is_active(tag_map),
                tag.id == tag_map.tag_id,
                tag.deleted_at.is_(None),
                tag.enabled.is_(True),
                or_(
                    and_(
                        RoleRequest.request_ownership.is_(True),
                        tag.constraints[Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY].as_boolean().is_(True),
                    ),
                    and_(
                        RoleRequest.request_ownership.is_(False),
                        tag.constraints[Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY].as_boolean().is_(True),
                    ),
                ),
            ),
        )
        conditions.append(not_(blocked_by_tag))
    return and_(*conditions)


def is_assigned_reviewer(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """SQL condition: the reviewer is an assigned reviewer of the request row.

    The condition refers to `request_cls`, so use it in a query that selects
    from that model. For example:
    `select(AccessRequest).where(is_assigned_reviewer(AccessRequest, user_id))`.

    Args:
        request_cls: `AccessRequest`, `RoleRequest`, or `GroupRequest`.
        reviewer_id: A user id, or a user-id column for a correlated query.

    Returns:
        True for rows where the reviewer is eligible at some owner level and
        no nearer level has an eligible reviewer.
    """
    clauses: list[ColumnElement[bool]] = []
    no_one_nearer: list[ColumnElement[bool]] = []
    for _level, ownership_condition in _owner_levels(request_cls):
        clauses.append(and_(_is_eligible(request_cls, ownership_condition, reviewer_id), *no_one_nearer))
        candidate = aliased(OktaUser)
        no_one_nearer.append(not_(_exists((candidate,), _is_eligible(request_cls, ownership_condition, candidate.id))))
    return or_(*clauses)


async def get_eligible_reviewers_by_level(request: AnyRequest) -> list[OwnerLevelReviewers]:
    """Return the request's eligible reviewers at each owner level, nearest first.

    Each user is listed once, at the nearest level where they are eligible.
    Levels with no eligible reviewer are left out. So the first entry, if
    any, holds the assigned reviewers. The rest show a requester who to
    contact to escalate.

    Args:
        request: A persisted access, role, or group request.

    Returns:
        One entry per level that has an eligible reviewer, nearest first.
        Empty when no one is eligible.
    """
    request_cls = type(request)
    levels: list[OwnerLevelReviewers] = []
    listed: set[str] = set()
    for level, ownership_condition in _owner_levels(request_cls):
        stmt = (
            select(OktaUser)
            .where(OktaUser.deleted_at.is_(None))
            .where(
                _exists(
                    (request_cls,),
                    request_cls.id == request.id,
                    _is_eligible(request_cls, ownership_condition, OktaUser.id),
                )
            )
            .order_by(OktaUser.email)
        )
        reviewers = [u for u in await db.session.scalars(stmt) if u.id not in listed]
        if reviewers:
            listed.update(u.id for u in reviewers)
            levels.append(OwnerLevelReviewers(level, reviewers))
    return levels


async def get_assigned_reviewers(request: AnyRequest) -> list[OktaUser]:
    """Return the request's assigned reviewers.

    These are the eligible reviewers at the nearest owner level that has
    any. A user is in the result exactly when `is_assigned_reviewer` holds
    for them and this request.

    Args:
        request: A persisted access, role, or group request.

    Returns:
        The assigned reviewers, ordered by email. Empty when no one is eligible.
    """
    levels = await get_eligible_reviewers_by_level(request)
    return levels[0].reviewers if levels else []
