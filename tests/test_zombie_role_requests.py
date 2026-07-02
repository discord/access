"""unmanage_group / delete_group must reject pending RoleRequests for the group.

Guards against a leftover RoleRequest surviving the operation in PENDING. If one
did, it would be invisible until the syncer re-managed the group (re-derives
is_managed) or resurrected a soft-deleted one (clears deleted_at on the same
id), at which point it would become approvable under whatever tag/constraint
state existed at that later moment.

These assert that pending RoleRequests touching the group are resolved at
unmanage/delete time, both where the group is the requested target and (when the
group is a role) where it is the requester role.
"""

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, OktaGroup, OktaUser, RoleGroup
from api.operations import CreateRoleRequest, DeleteGroup, ModifyGroupUsers, UnmanageGroup
from api.services import okta


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # Keep the operations off the network. patch.object auto-detects the async
    # functions and substitutes AsyncMocks.
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")
    mocker.patch.object(okta, "delete_group")


async def _access_owner(db: Db) -> OktaUser:
    owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    assert owner is not None
    return owner


async def _pending_role_request(
    db: Db, *, requester_role: RoleGroup, requested_group: OktaGroup, requester_user: OktaUser
):
    # Requester must own the role to file a role request for it.
    await ModifyGroupUsers(group=requester_role, owners_to_add=[requester_user.id], sync_to_okta=False).execute()
    role_request = await CreateRoleRequest(
        requester_user=requester_user,
        requester_role=requester_role,
        requested_group=requested_group,
        request_reason="role needs this group",
    ).execute()
    assert role_request is not None
    assert role_request.status == AccessRequestStatus.PENDING
    return role_request


# ---------------------------------------------------------------------------
# Group is the requested target of the RoleRequest
# ---------------------------------------------------------------------------


async def test_unmanage_group_rejects_pending_role_request_for_group(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()
    role_request = await _pending_role_request(
        db, requester_role=role_group, requested_group=okta_group, requester_user=user
    )

    # Group transitions to unmanaged (as the syncer does before UnmanageGroup).
    okta_group.is_managed = False
    await db.session.commit()

    await UnmanageGroup(group=okta_group, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None


async def test_delete_group_rejects_pending_role_request_for_group(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()
    role_request = await _pending_role_request(
        db, requester_role=role_group, requested_group=okta_group, requester_user=user
    )

    await DeleteGroup(group=okta_group, sync_to_okta=False, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None


# ---------------------------------------------------------------------------
# Group is the requester role of the RoleRequest
# ---------------------------------------------------------------------------


async def test_unmanage_group_rejects_pending_role_request_where_group_is_requester_role(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()
    # role_group is the requester; okta_group is the requested target.
    role_request = await _pending_role_request(
        db, requester_role=role_group, requested_group=okta_group, requester_user=user
    )

    role_group.is_managed = False
    await db.session.commit()

    await UnmanageGroup(group=role_group, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None


async def test_delete_group_rejects_pending_role_request_where_group_is_requester_role(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    await db.session.commit()
    role_request = await _pending_role_request(
        db, requester_role=role_group, requested_group=okta_group, requester_user=user
    )

    await DeleteGroup(group=role_group, sync_to_okta=False, current_user_id=(await _access_owner(db)).id).execute()

    await db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None
