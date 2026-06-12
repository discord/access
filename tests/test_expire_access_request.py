from datetime import datetime, timedelta, timezone


from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser
from api.extensions import Db
from api.syncer import expire_access_requests


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
