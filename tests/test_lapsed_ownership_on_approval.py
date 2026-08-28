"""Approving a group request must not mint an already-expired ownership row.

ApproveGroupRequest falls back to `requested_ownership_ending_at` when the
approver supplies no resolved value, and `coalesce_ended_at` only ever moves a
date earlier, so a stale request would otherwise create a group whose sole
ownership row is expired on arrival -- i.e. a group with no owners, whose future
approvals route to the app owner or an Access admin rather than the requester.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from okta.models import Group
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.exceptions import InvalidRequestError
from api.extensions import Db
from api.models import AccessRequestStatus, GroupRequest, OktaGroup, OktaUser
from api.models.okta_group import get_group_managers
from api.operations import ApproveGroupRequest, CreateGroupRequest
from api.services import okta


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "create_group", side_effect=lambda name, desc: Group.from_dict({"id": uuid.uuid4().hex}))


async def _access_owner(db: Db) -> OktaUser:
    owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    assert owner is not None
    return owner


async def _group_request(db: Db, user: OktaUser, name: str) -> GroupRequest:
    request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name=name,
        requested_group_description="test",
        requested_group_type="okta_group",
        request_reason="need a group",
    ).execute()
    assert request is not None
    return request


async def test_approval_rejected_when_requested_ownership_window_has_passed(db: Db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()
    approver = await _access_owner(db)
    request = await _group_request(db, user, "Lapsed")
    request.requested_ownership_ending_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db.session.commit()
    # Captured before expire_all() below: reading request.id afterwards would
    # refresh an expired instance outside a greenlet (MissingGreenlet).
    request_id = request.id

    with pytest.raises(InvalidRequestError) as exc:
        await ApproveGroupRequest(group_request=request_id, approver_user=approver, approval_reason="ok").execute()
    assert "already passed" in str(exc.value)

    # The guard raises mid-transaction, so unwind it the way the request teardown
    # would before inspecting committed state.
    await db.session.rollback()

    # Nothing was created, and the request is still actionable.
    db.session.expire_all()
    assert (await db.session.scalars(select(OktaGroup).where(OktaGroup.name == "Lapsed"))).first() is None
    row = await db.session.get(GroupRequest, request_id)
    assert row.status == AccessRequestStatus.PENDING
    assert row.resolved_at is None


async def test_approval_rejected_when_resolved_ownership_window_has_passed(db: Db, user: OktaUser) -> None:
    """An explicitly supplied past date is refused too, not just an inherited one."""
    db.session.add(user)
    await db.session.commit()
    approver = await _access_owner(db)
    request = await _group_request(db, user, "LapsedResolved")
    request.requested_ownership_ending_at = datetime.now(timezone.utc) + timedelta(days=30)
    request.resolved_ownership_ending_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.session.commit()

    with pytest.raises(InvalidRequestError):
        await ApproveGroupRequest(group_request=request.id, approver_user=approver, approval_reason="ok").execute()


async def test_approval_succeeds_with_a_future_ownership_window(db: Db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()
    approver = await _access_owner(db)
    request = await _group_request(db, user, "Future")
    request.requested_ownership_ending_at = datetime.now(timezone.utc) + timedelta(days=30)
    await db.session.commit()

    await ApproveGroupRequest(group_request=request.id, approver_user=approver, approval_reason="ok").execute()

    db.session.expire_all()
    group = (await db.session.scalars(select(OktaGroup).where(OktaGroup.name == "Future"))).first()
    assert group is not None
    managers = await get_group_managers(group.id)
    assert [m.id for m in managers] == [user.id]


async def test_approval_succeeds_with_an_indefinite_ownership_window(db: Db, user: OktaUser) -> None:
    """No window at all is untouched by the guard; it means indefinite ownership."""
    db.session.add(user)
    await db.session.commit()
    approver = await _access_owner(db)
    request = await _group_request(db, user, "Indefinite")
    assert request.requested_ownership_ending_at is None

    await ApproveGroupRequest(group_request=request.id, approver_user=approver, approval_reason="ok").execute()

    db.session.expire_all()
    group = (await db.session.scalars(select(OktaGroup).where(OktaGroup.name == "Indefinite"))).first()
    assert group is not None
    managers = await get_group_managers(group.id)
    assert [m.id for m in managers] == [user.id]
