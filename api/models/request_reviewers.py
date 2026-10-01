"""Who reviews a request.

This module is the single definition of request reviewers. Notifications,
"Assigned to Me" views, and the request detail pages all read it.

Every request has an ordered list of owner levels, nearest first:

- An access request, individual (`AccessRequest`) or role-based
  (`RoleRequest`), for group G: the owners of G; then the owners of G's
  app, if G is an app group; then Access admins.
- A group request (`GroupRequest`) to create G: the owners of the requested
  app, if G is an app group; then Access admins.

A user at a level is an eligible reviewer unless they are the requester,
they are deleted, or, for anyone but an Access admin, approving would add
them to G against an enabled `disallow_self_add_*` tag on G. Only a
role-based request can do that, when the reviewer is a member of the
requesting role; this mirrors `CheckForSelfAdd.execute_for_role`, which
blocks such an approval.

A request's assigned reviewers are the eligible reviewers at the nearest
level that has any. Each level is written once, as a SQL condition
correlated to the request row, so the same definition answers both "who
reviews this request" (`get_assigned_reviewers`) and "which requests does
this user review" (`assigned_reviewer_condition`).

Assignment does not decide who may resolve a request; the approve and
reject routes check that separately.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, cast

from sqlalchemy import ColumnElement, ColumnExpressionArgument, and_, exists, false, func, not_, or_, select
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
_Owns = Callable[[RequestModel, ReviewerId], ColumnElement[bool]]


@dataclass(frozen=True)
class AssignedReviewers:
    """The eligible reviewers at a request's nearest owner level that has any.

    Attributes:
        owner_level: That level, or None when no level has an eligible reviewer.
        reviewers: The eligible reviewers at `owner_level`; empty when it is None.
    """

    owner_level: OwnerLevel | None
    reviewers: list[OktaUser]


@dataclass(frozen=True)
class OwnerLevelReviewers:
    """Everyone who owns a request at one owner level, eligible or not.

    Attributes:
        owner_level: The level.
        reviewers: Its owners, which may include the requester.
    """

    owner_level: OwnerLevel
    reviewers: list[OktaUser]


def _exists(froms: tuple[Any, ...], *conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    """EXISTS over `froms`, correlated to every other table its conditions name.

    SQLAlchemy auto-correlates a subquery only to the query directly around
    it, so a nested EXISTS naming the request row or the reviewer column from
    further out would get its own uncorrelated copy of that table instead.
    Selecting from `froms` explicitly also renders a flat-aliased `AppGroup`
    as its `okta_group JOIN app_group`, rather than as two unjoined tables.
    """
    return exists().select_from(*froms).where(*conditions).correlate_except(*froms)


def _is_active(row: type[OktaUserGroupMember] | type[OktaGroupTagMap]) -> ColumnElement[bool]:
    """The row has not ended."""
    return or_(row.ended_at.is_(None), row.ended_at > func.now())


def _owns_app(app_id: ColumnExpressionArgument[str | None], reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns an active owners group of the app `app_id`."""
    owners_group = aliased(AppGroup, flat=True)
    ownership = aliased(OktaUserGroupMember)
    return _exists(
        (owners_group, ownership),
        owners_group.app_id == app_id,
        owners_group.is_owner.is_(True),
        owners_group.deleted_at.is_(None),
        ownership.group_id == owners_group.id,
        ownership.user_id == reviewer_id,
        ownership.is_owner.is_(True),
        _is_active(ownership),
    )


def _is_access_admin(reviewer_id: ReviewerId) -> ColumnElement[bool]:
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


def _owns_requested_group(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns the access request's group."""
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


def _owns_app_of_requested_group(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns the app of the access request's group, when that group is an app group."""
    assert request_cls is not GroupRequest
    cls = cast(type[AccessRequest] | type[RoleRequest], request_cls)
    group = aliased(AppGroup, flat=True)
    return _exists(
        (group,),
        group.id == cls.requested_group_id,
        group.deleted_at.is_(None),
        _owns_app(group.app_id, reviewer_id),
    )


def _owns_requested_app(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns the app a group request asks to create an app group in."""
    assert request_cls is GroupRequest
    return and_(
        GroupRequest.requested_group_type == "app_group",
        _owns_app(GroupRequest.requested_app_id, reviewer_id),
    )


def _owns_access(_request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer is an Access admin."""
    return _is_access_admin(reviewer_id)


def _owner_levels(request_cls: RequestModel) -> list[tuple[OwnerLevel, _Owns]]:
    """The owner levels for a kind of request, nearest first."""
    if request_cls is GroupRequest:
        return [("app_owners", _owns_requested_app), ("access_admins", _owns_access)]
    return [
        ("group_owners", _owns_requested_group),
        ("app_owners", _owns_app_of_requested_group),
        ("access_admins", _owns_access),
    ]


def _blocked_by_self_add_tag(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """Approving this role request would add a non-admin reviewer to the group against a tag."""
    if request_cls is not RoleRequest:
        return false()
    role_membership = aliased(OktaUserGroupMember)
    group = aliased(OktaGroup)
    tag_map = aliased(OktaGroupTagMap)
    tag = aliased(Tag)
    return and_(
        not_(_is_access_admin(reviewer_id)),
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


def _eligible_at(request_cls: RequestModel, owns: _Owns, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """The reviewer owns at this level and is eligible to review the request."""
    reviewer = aliased(OktaUser)
    return and_(
        owns(request_cls, reviewer_id),
        request_cls.requester_user_id != reviewer_id,
        _exists((reviewer,), reviewer.id == reviewer_id, reviewer.deleted_at.is_(None)),
        not_(_blocked_by_self_add_tag(request_cls, reviewer_id)),
    )


def _anyone_eligible_at(request_cls: RequestModel, owns: _Owns) -> ColumnElement[bool]:
    """Some user is eligible at this level."""
    candidate = aliased(OktaUser)
    return _exists((candidate,), _eligible_at(request_cls, owns, candidate.id))


def assigned_reviewer_condition(request_cls: RequestModel, reviewer_id: ReviewerId) -> ColumnElement[bool]:
    """SQL condition: the reviewer is an assigned reviewer of the request row.

    The condition is correlated to `request_cls`, so use it in a query that
    selects from that model, e.g.
    `select(AccessRequest).where(assigned_reviewer_condition(AccessRequest, user_id))`.

    Args:
        request_cls: `AccessRequest`, `RoleRequest`, or `GroupRequest`.
        reviewer_id: A user id, or a user-id column for a correlated query.

    Returns:
        True for rows where the reviewer is eligible at some owner level and
        no nearer level has an eligible reviewer.
    """
    clauses: list[ColumnElement[bool]] = []
    no_one_nearer: list[ColumnElement[bool]] = []
    for _level, owns in _owner_levels(request_cls):
        clauses.append(and_(_eligible_at(request_cls, owns, reviewer_id), *no_one_nearer))
        no_one_nearer.append(not_(_anyone_eligible_at(request_cls, owns)))
    return or_(*clauses)


async def _users_at_level(request: AnyRequest, owns: _Owns, *, eligible_only: bool) -> list[OktaUser]:
    """Active users who own at one level for `request`, optionally only the eligible ones."""
    request_cls = type(request)

    def condition(reviewer_id: ReviewerId) -> ColumnElement[bool]:
        return _eligible_at(request_cls, owns, reviewer_id) if eligible_only else owns(request_cls, reviewer_id)

    stmt = (
        select(OktaUser)
        .where(OktaUser.deleted_at.is_(None))
        .where(_exists((request_cls,), request_cls.id == request.id, condition(OktaUser.id)))
        .order_by(OktaUser.email)
    )
    return list((await db.session.scalars(stmt)).all())


async def get_assigned_reviewers(request: AnyRequest) -> AssignedReviewers:
    """Return the eligible reviewers at the request's nearest owner level that has any.

    Uses the same per-level conditions as `assigned_reviewer_condition`, so a
    user is in the result exactly when that condition holds for them and this
    request.

    Args:
        request: A persisted access, role, or group request.

    Returns:
        The level and its eligible reviewers, or `AssignedReviewers(None, [])`.
    """
    for level, owns in _owner_levels(type(request)):
        reviewers = await _users_at_level(request, owns, eligible_only=True)
        if reviewers:
            return AssignedReviewers(level, reviewers)
    return AssignedReviewers(None, [])


async def _level_applies(request: AnyRequest, level: OwnerLevel) -> bool:
    """Whether the request has this level; only app groups have an app owners level."""
    if level != "app_owners":
        return True
    if isinstance(request, GroupRequest):
        return request.requested_group_type == "app_group" and request.requested_app_id is not None
    # None: not an app group. True: the app's owners group itself, whose owners
    # are the app owners and are already listed as its group owners.
    is_owners_group = await db.session.scalar(
        select(AppGroup.is_owner).where(AppGroup.id == request.requested_group_id)
    )
    return is_owners_group is False


async def get_possible_reviewers_by_level(request: AnyRequest) -> list[OwnerLevelReviewers]:
    """Return everyone who owns the request at each owner level, nearest first.

    Ignores eligibility, so a requester who owns at a level is listed there.
    Lists a level that applies even when it has no owners; omits the app
    owners level for requests that do not concern an app group, and for the
    app's owners group itself.

    Args:
        request: A persisted access, role, or group request.

    Returns:
        One entry per applicable level, nearest first. Access admins are always last.
    """
    levels: list[OwnerLevelReviewers] = []
    for level, owns in _owner_levels(type(request)):
        if await _level_applies(request, level):
            levels.append(OwnerLevelReviewers(level, await _users_at_level(request, owns, eligible_only=False)))
    return levels


async def snapshot_assigned_reviewers(requests: Iterable[AnyRequest]) -> dict[str, list[OktaUser]]:
    """Return each request's assigned reviewers, keyed by request id.

    Operations that resolve requests as a side effect of changing ownership
    call this before the change: approving can change who is assigned (a
    role granted ownership of a group makes its members owners of that
    group), and the close notification goes to the reviewers assigned while
    the request was open.

    Args:
        requests: Persisted access, role, or group requests.

    Returns:
        Request id to that request's assigned reviewers.
    """
    return {request.id: (await get_assigned_reviewers(request)).reviewers for request in requests}
