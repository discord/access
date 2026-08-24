from datetime import datetime, timedelta, timezone

from api.extensions import Db
from api.models import AccessRequestStatus, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.syncer import expire_role_requests


async def _persist(
    db: Db, role_request: RoleRequest, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> str:
    """Persist a role request wired to a role, a target group, and a requester."""
    db.session.add(user)
    db.session.add(role_group)
    db.session.add(okta_group)
    await db.session.commit()
    role_request.requester_user_id = user.id
    role_request.requester_role_id = role_group.id
    role_request.requested_group_id = okta_group.id
    db.session.add(role_request)
    await db.session.commit()
    return role_request.id


async def test_no_expire_new_role_request(
    db: Db, role_request: RoleRequest, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    role_request_id = await _persist(db, role_request, role_group, okta_group, user)

    await expire_role_requests()

    row = await db.session.get(RoleRequest, role_request_id)
    assert row.status == AccessRequestStatus.PENDING
    assert row.resolved_at is None


async def test_expire_old_role_request(
    db: Db, role_request: RoleRequest, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    role_request.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    role_request_id = await _persist(db, role_request, role_group, okta_group, user)

    await expire_role_requests()

    # The reject operation expired this row's attributes; expire_all so the
    # awaited get() refreshes it instead of lazy-loading on attribute access.
    db.session.expire_all()
    row = await db.session.get(RoleRequest, role_request_id)
    assert row.status == AccessRequestStatus.REJECTED
    assert row.resolved_at is not None
    assert row.resolver_user_id is None


async def test_expire_role_request_with_lapsed_window(
    db: Db, role_request: RoleRequest, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser
) -> None:
    """A role request past its request_ending_at is moot: approving it would
    mint an already-expired RoleGroupMap."""
    role_request.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    role_request.request_ending_at = datetime.now(timezone.utc) - timedelta(hours=12)
    role_request_id = await _persist(db, role_request, role_group, okta_group, user)

    await expire_role_requests()

    db.session.expire_all()
    row = await db.session.get(RoleRequest, role_request_id)
    assert row.status == AccessRequestStatus.REJECTED
    assert row.resolved_at is not None
