from datetime import datetime, timedelta, timezone

from pytest_mock import MockerFixture

from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser
from api.extensions import Db
from api.operations import RejectAccessRequest
from api.syncer import expire_access_requests
from tests.factories import AccessRequestFactory


async def test_no_expire_new_access_request(
    db: Db, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    await db.session.commit()

    access_request_id = access_request.id

    await expire_access_requests()

    access_request = await db.session.get(AccessRequest, access_request_id)
    assert access_request.status == AccessRequestStatus.PENDING
    assert access_request.resolved_at is None


async def test_expire_old_access_request(
    db: Db, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    access_request.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    await db.session.commit()

    access_request_id = access_request.id

    await expire_access_requests()

    # The reject operation expired this row's attributes via a
    # synchronize_session="fetch" UPDATE; expire_all so the awaited get()
    # refreshes it instead of lazy-loading on attribute access.
    db.session.expire_all()
    access_request = await db.session.get(AccessRequest, access_request_id)
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None


async def test_expire_access_requests_isolates_failures(
    db: Db,
    access_request: AccessRequest,
    okta_group: OktaGroup,
    user: OktaUser,
    mocker: MockerFixture,
) -> None:
    """One request that fails to reject must not abort the rest of the sweep.

    Without per-request isolation a single poisoned row aborts expiration for
    every other row, and does so again on every subsequent sync run.
    """
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()

    stale = datetime.now(timezone.utc) - timedelta(days=30)
    ids = []
    for _ in range(3):
        req = AccessRequestFactory.build()
        req.created_at = stale
        req.requested_group_id = okta_group.id
        req.requester_user_id = user.id
        db.session.add(req)
        ids.append(req.id)
    await db.session.commit()

    # Fail the first reject only; the other two must still be expired.
    real_execute = RejectAccessRequest.execute
    calls = {"n": 0}

    async def flaky(self: RejectAccessRequest) -> AccessRequest:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real_execute(self)

    mocker.patch.object(RejectAccessRequest, "execute", flaky)

    await expire_access_requests()

    db.session.expire_all()
    statuses = []
    for request_id in ids:
        row = await db.session.get(AccessRequest, request_id)
        statuses.append(row.status)

    assert statuses.count(AccessRequestStatus.REJECTED) == 2
    assert statuses.count(AccessRequestStatus.PENDING) == 1


async def test_expire_old_temporary_access_request(
    db: Db, access_request: AccessRequest, okta_group: OktaGroup, user: OktaUser
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    access_request.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    access_request.request_ending_at = datetime.now(timezone.utc) - timedelta(hours=12)
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    await db.session.commit()

    access_request_id = access_request.id

    await expire_access_requests()

    # The reject operation expired this row's attributes via a
    # synchronize_session="fetch" UPDATE; expire_all so the awaited get()
    # refreshes it instead of lazy-loading on attribute access.
    db.session.expire_all()
    access_request = await db.session.get(AccessRequest, access_request_id)
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None
