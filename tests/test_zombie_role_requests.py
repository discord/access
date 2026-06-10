"""Spec tests: unmanage_group / delete_group must reject pending RoleRequests.

Both operations reject pending AccessRequests for the affected group but ignore
pending RoleRequests, leaving them PENDING. ApproveRoleRequest then silently
no-ops while the group is unmanaged/deleted, but the syncer can re-manage a
group (update_okta_group re-derives is_managed) or resurrect a soft-deleted one
(clears deleted_at on the same id), at which point the leftover RoleRequest
becomes approvable under whatever tag/constraint state exists at that later
moment.

These encode the fix contract: pending RoleRequests touching the group must be
rejected, both where the group is the requested target and (when the group is a
role) where it is the requester role. RED until the rejection loop is added.
"""

import pytest
from pytest_mock import MockerFixture

from api.config import settings
from api.extensions import Db
from api.models import AccessRequestStatus, OktaGroup, OktaUser, RoleGroup
from api.operations import CreateRoleRequest, DeleteGroup, ModifyGroupUsers, UnmanageGroup
from api.services import okta


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # Keep the operations off the network. patch.object auto-detects the async
    # functions and substitutes AsyncMocks.
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")
    mocker.patch.object(okta, "async_remove_user_from_group")
    mocker.patch.object(okta, "async_remove_owner_from_group")
    mocker.patch.object(okta, "async_delete_group")


def _access_owner(db: Db) -> OktaUser:
    owner = db.session.query(OktaUser).filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    assert owner is not None
    return owner


def _pending_role_request(db: Db, *, requester_role: RoleGroup, requested_group: OktaGroup, requester_user: OktaUser):
    # Requester must own the role to file a role request for it.
    ModifyGroupUsers(group=requester_role, owners_to_add=[requester_user.id], sync_to_okta=False).execute()
    role_request = CreateRoleRequest(
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


def test_unmanage_group_rejects_pending_role_request_for_group(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    role_request = _pending_role_request(db, requester_role=role_group, requested_group=okta_group, requester_user=user)

    # Group transitions to unmanaged (as the syncer does before UnmanageGroup).
    okta_group.is_managed = False
    db.session.commit()

    UnmanageGroup(group=okta_group, current_user_id=_access_owner(db).id).execute()

    db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None


def test_delete_group_rejects_pending_role_request_for_group(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    role_request = _pending_role_request(db, requester_role=role_group, requested_group=okta_group, requester_user=user)

    DeleteGroup(group=okta_group, sync_to_okta=False, current_user_id=_access_owner(db).id).execute()

    db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None


# ---------------------------------------------------------------------------
# Group is the requester role of the RoleRequest
# ---------------------------------------------------------------------------


def test_unmanage_group_rejects_pending_role_request_where_group_is_requester_role(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    # role_group is the requester; okta_group is the requested target.
    role_request = _pending_role_request(db, requester_role=role_group, requested_group=okta_group, requester_user=user)

    role_group.is_managed = False
    db.session.commit()

    UnmanageGroup(group=role_group, current_user_id=_access_owner(db).id).execute()

    db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None


def test_delete_group_rejects_pending_role_request_where_group_is_requester_role(
    db: Db, user: OktaUser, role_group: RoleGroup, okta_group: OktaGroup
) -> None:
    db.session.add_all([user, role_group, okta_group])
    db.session.commit()
    role_request = _pending_role_request(db, requester_role=role_group, requested_group=okta_group, requester_user=user)

    DeleteGroup(group=role_group, sync_to_okta=False, current_user_id=_access_owner(db).id).execute()

    db.session.refresh(role_request)
    assert role_request.status == AccessRequestStatus.REJECTED
    assert role_request.resolved_at is not None
