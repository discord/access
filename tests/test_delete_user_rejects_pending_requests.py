"""DeleteUser rejects the deleted user's pending requests.

AccessRequests were already rejected on delete; these assert RoleRequests and
GroupRequests are handled the same way (the AccessRequest test is the baseline).
"""

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, OktaGroup, OktaUser, RoleGroup, RoleRequest
from api.operations import (
    CreateAccessRequest,
    CreateGroupRequest,
    CreateRoleRequest,
    DeleteUser,
    ModifyGroupUsers,
)
from api.services import okta

DELETION_REASON = "Closed because the requestor was deleted"


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # Keep operations off the network.
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")


async def _access_owner(db: Db) -> OktaUser:
    owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    assert owner is not None
    return owner


async def test_delete_user_rejects_pending_access_request(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add_all([user, okta_group])
    await db.session.commit()

    access_request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_reason="need this group",
    ).execute()
    assert access_request is not None
    # Widened local so the precondition check doesn't narrow the attribute type
    # and make the post-refresh REJECTED assertion look non-overlapping to mypy.
    created_status: AccessRequestStatus = access_request.status
    assert created_status == AccessRequestStatus.PENDING

    await DeleteUser(user=user, sync_to_okta=False, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(access_request)
    assert access_request.status == AccessRequestStatus.REJECTED
    assert access_request.resolved_at is not None
    assert access_request.resolution_reason == DELETION_REASON


async def test_delete_user_rejects_pending_role_request(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()

    # Requester must own the role to file a role request for it.
    await ModifyGroupUsers(group=role_group, owners_to_add=[user.id], sync_to_okta=False).execute()
    role_request = await CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_reason="role needs this group",
    ).execute()
    assert role_request is not None
    created_status: AccessRequestStatus = role_request.status
    assert created_status == AccessRequestStatus.PENDING

    await DeleteUser(user=user, sync_to_okta=False, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None
    assert role_request.resolution_reason == DELETION_REASON


async def test_delete_user_rejects_pending_role_request_on_cold_session(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    """Cold-session regression for the RejectRoleRequest audit-log eager-load.

    `RejectRoleRequest`'s audit log serializes `RoleRequest.requester_role`,
    which is `lazy="raise_on_sql"`. The warm test above passes because the role
    group is resident in the shared session, but the `access sync` cronjob
    deletes users on a session where it isn't — so the reject's own query must
    eager-load `requester_role` or the audit dump raises `InvalidRequestError`.
    `expunge_all()` before the delete forces that cold load here; without the
    eager-load this raises rather than rejecting the request.
    """
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()

    # Requester must own the role to file a role request for it.
    await ModifyGroupUsers(group=role_group, owners_to_add=[user.id], sync_to_okta=False).execute()
    role_request = await CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_reason="role needs this group",
    ).execute()
    assert role_request is not None
    role_request_id = role_request.id
    user_id = user.id
    owner_id = (await _access_owner(db)).id

    # Cold session: force RejectRoleRequest to re-load the role request with no
    # resident role group, mirroring the sync cronjob that surfaced this.
    db.session.expunge_all()

    await DeleteUser(user=user_id, sync_to_okta=False, current_user_id=owner_id).execute()

    rejected = await db.session.get(RoleRequest, role_request_id)
    assert rejected is not None
    assert rejected.status == AccessRequestStatus.REJECTED
    assert rejected.resolution_reason == DELETION_REASON


async def test_delete_user_rejects_pending_group_request(db: Db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()

    group_request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Test Group",
        requested_group_description="Test Description",
        requested_group_type="okta_group",
        request_reason="need this group",
    ).execute()
    assert group_request is not None
    created_status: AccessRequestStatus = group_request.status
    assert created_status == AccessRequestStatus.PENDING

    await DeleteUser(user=user, sync_to_okta=False, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(group_request)
    assert group_request.status == AccessRequestStatus.REJECTED
    assert group_request.resolved_at is not None
    assert group_request.resolution_reason == DELETION_REASON
