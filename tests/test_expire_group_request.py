from datetime import datetime, timedelta, timezone

import pytest

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, GroupRequest, OktaUser
from api.syncer import expire_group_requests


async def _persist(db: Db, group_request: GroupRequest, user: OktaUser) -> str:
    db.session.add(user)
    await db.session.commit()
    group_request.requester_user_id = user.id
    db.session.add(group_request)
    await db.session.commit()
    return group_request.id


async def test_no_expire_new_group_request(db: Db, group_request: GroupRequest, user: OktaUser) -> None:
    group_request_id = await _persist(db, group_request, user)

    await expire_group_requests()

    row = await db.session.get(GroupRequest, group_request_id)
    assert row.status == AccessRequestStatus.PENDING
    assert row.resolved_at is None


async def test_expire_old_group_request(db: Db, group_request: GroupRequest, user: OktaUser) -> None:
    group_request.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    group_request_id = await _persist(db, group_request, user)

    await expire_group_requests()

    # The reject operation expired this row's attributes; expire_all so the
    # awaited get() refreshes it instead of lazy-loading on attribute access.
    db.session.expire_all()
    row = await db.session.get(GroupRequest, group_request_id)
    assert row.status == AccessRequestStatus.REJECTED
    assert row.resolved_at is not None
    assert row.resolver_user_id is None


async def test_no_expire_group_request_with_lapsed_ownership_window(
    db: Db, group_request: GroupRequest, user: OktaUser
) -> None:
    """A lapsed requested_ownership_ending_at must NOT expire the request.

    Deliberate asymmetry with access and role requests, and a policy call
    rather than a claim that nothing can go wrong. A group request's payload is
    the group itself; throwing the whole ask away over a stale secondary field
    loses more than it protects. For an access or role request the window IS
    the ask, which is why those get a second sweep.

    What this leaves to the approve path: ApproveGroupRequest falls back to
    requested_ownership_ending_at when the approver supplies no resolved value,
    so approving a long-stale request could write an already-expired ownership
    row and leave the new group unowned. That is handled there (the approval is
    refused outright); it was never a reason to expire the request here.
    """
    group_request.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    group_request.requested_ownership_ending_at = datetime.now(timezone.utc) - timedelta(hours=12)
    group_request_id = await _persist(db, group_request, user)

    await expire_group_requests()

    row = await db.session.get(GroupRequest, group_request_id)
    assert row.status == AccessRequestStatus.PENDING
    assert row.resolved_at is None


async def test_group_request_uses_its_own_cutoff(
    db: Db, group_request: GroupRequest, user: OktaUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MAX_GROUP_REQUEST_AGE_SECONDS governs group requests independently."""
    monkeypatch.setattr(settings, "MAX_ACCESS_REQUEST_AGE_SECONDS", 7 * 24 * 60 * 60)
    monkeypatch.setattr(settings, "MAX_GROUP_REQUEST_AGE_SECONDS", 30 * 24 * 60 * 60)

    # 10 days old: past the access cutoff, inside the longer group cutoff.
    group_request.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    group_request_id = await _persist(db, group_request, user)

    await expire_group_requests()

    row = await db.session.get(GroupRequest, group_request_id)
    assert row.status == AccessRequestStatus.PENDING


async def test_never_disables_group_age_expiry(
    db: Db, group_request: GroupRequest, user: OktaUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAX_GROUP_REQUEST_AGE_SECONDS", "never")
    group_request.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    group_request_id = await _persist(db, group_request, user)

    await expire_group_requests()

    row = await db.session.get(GroupRequest, group_request_id)
    assert row.status == AccessRequestStatus.PENDING
    assert row.resolved_at is None


async def test_group_expiry_is_independent_of_the_access_cutoff(
    db: Db, group_request: GroupRequest, user: OktaUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabling the access cutoff must not disable the group one."""
    monkeypatch.setattr(settings, "MAX_ACCESS_REQUEST_AGE_SECONDS", "never")
    group_request.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    group_request_id = await _persist(db, group_request, user)

    await expire_group_requests()

    db.session.expire_all()
    row = await db.session.get(GroupRequest, group_request_id)
    assert row.status == AccessRequestStatus.REJECTED
