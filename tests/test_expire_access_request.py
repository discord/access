from datetime import datetime, timedelta, timezone

from pytest_mock import MockerFixture

from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser
from api.extensions import Db
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
    """One request that fails mid-write must not abort or corrupt the rest of the sweep.

    The failure is injected at the operation's own `commit`, i.e. *after* it has
    assigned status/resolved_at/resolver_user_id/resolution_reason to the
    session. That is the state the rollback in `_expire_each` exists for, and
    the only state that distinguishes it from a no-op: without the rollback the
    dirty REJECTED row stays pending in the session and the next iteration's
    `SELECT ... FOR UPDATE` autoflushes it, silently rejecting the very request
    the sweep logged as skipped. Injecting before any SQL is emitted would leave
    the session clean and pass with or without the rollback.
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

    # Fail the first commit the sweep attempts; let every later one through.
    real_commit = db.session.commit
    calls = {"n": 0}

    async def flaky_commit() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("commit blew up mid-write")
        await real_commit()

    mocker.patch.object(db.session, "commit", flaky_commit)

    await expire_access_requests()

    mocker.stopall()
    db.session.expire_all()
    statuses = []
    for request_id in ids:
        row = await db.session.get(AccessRequest, request_id)
        statuses.append(row.status)

    # Two expired; the one whose commit failed is still PENDING rather than
    # having been written by a later iteration's autoflush.
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
