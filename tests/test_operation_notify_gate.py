"""`notify=False` must suppress the completion DM for every operation that
takes a `notify` flag, while the underlying state change (APPROVED / REJECTED)
still happens — `notify` controls only the notification, never the action.

`ModifyRoleGroups` is covered by its own cold-session tests in `test_role.py`
(it auto-approves both access *and* role requests); the cases here cover the
remaining notify-gated operations. Each request is genuinely resolved first, so
removing a gate would make its spy fire — the `call_count == 0` assertions are
not vacuous.
"""

import pytest
from okta.models import Group
from pytest_mock import MockerFixture

from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    GroupRequest,
    OktaGroup,
    OktaUser,
    RoleGroup,
    RoleRequest,
)
from api.operations import (
    ApproveAccessRequest,
    ApproveGroupRequest,
    ApproveRoleRequest,
    CreateAccessRequest,
    CreateGroupRequest,
    CreateRoleRequest,
    ModifyGroupUsers,
    RejectAccessRequest,
    RejectGroupRequest,
    RejectRoleRequest,
)
from api.plugins import get_notification_hook
from api.services import okta


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # Approve paths sync membership to Okta by default; keep it off the network.
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")
    mocker.patch.object(okta, "async_remove_user_from_group")
    mocker.patch.object(okta, "async_remove_owner_from_group")


def _access_owner(db: Db) -> OktaUser:
    # The bootstrap Access owner — a valid approver distinct from the requester.
    owner = db.session.query(OktaUser).filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    assert owner is not None
    return owner


def _pending_access_request(db: Db, user: OktaUser, group: OktaGroup) -> AccessRequest:
    request = CreateAccessRequest(
        requester_user=user, requested_group=group, request_ownership=False, request_reason="please"
    ).execute()
    assert request is not None
    return request


def _pending_role_request(db: Db, user: OktaUser, role: RoleGroup, group: OktaGroup) -> RoleRequest:
    # Requester must own the role to file a role request for it.
    ModifyGroupUsers(group=role, owners_to_add=[user.id], sync_to_okta=False).execute()
    request = CreateRoleRequest(
        requester_user=user,
        requester_role=role,
        requested_group=group,
        request_ownership=False,
        request_reason="please",
    ).execute()
    assert request is not None
    return request


def _pending_group_request(db: Db, user: OktaUser) -> GroupRequest:
    request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Notify Gate Group",
        requested_group_description="d",
        requested_group_type="okta_group",
        request_reason="please",
    ).execute()
    assert request is not None
    return request


def test_modify_group_users_notify_false_suppresses_access_request_completion(
    db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add_all([okta_group, user])
    db.session.commit()
    access_request = _pending_access_request(db, user, okta_group)

    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_request_completed")

    # Adding `user` as a member auto-approves their pending membership request.
    ModifyGroupUsers(group=okta_group.id, members_to_add=[user.id], sync_to_okta=False, notify=False).execute()

    assert db.session.get(AccessRequest, access_request.id).status == AccessRequestStatus.APPROVED
    assert spy.call_count == 0


def test_approve_access_request_notify_false_suppresses_completion(
    db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add_all([okta_group, user])
    db.session.commit()
    access_request = _pending_access_request(db, user, okta_group)

    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_request_completed")

    ApproveAccessRequest(access_request=access_request, approver_user=_access_owner(db), notify=False).execute()

    assert db.session.get(AccessRequest, access_request.id).status == AccessRequestStatus.APPROVED
    assert spy.call_count == 0


def test_reject_access_request_notify_false_suppresses_completion(
    db: Db, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add_all([okta_group, user])
    db.session.commit()
    access_request = _pending_access_request(db, user, okta_group)

    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_request_completed")

    RejectAccessRequest(access_request=access_request, current_user_id=_access_owner(db), notify=False).execute()

    assert db.session.get(AccessRequest, access_request.id).status == AccessRequestStatus.REJECTED
    assert spy.call_count == 0


def test_approve_role_request_notify_false_suppresses_completion(
    db: Db, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add_all([role_group, okta_group, user])
    db.session.commit()
    role_request = _pending_role_request(db, user, role_group, okta_group)

    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_role_request_completed")

    ApproveRoleRequest(role_request=role_request, approver_user=_access_owner(db), notify=False).execute()

    assert db.session.get(RoleRequest, role_request.id).status == AccessRequestStatus.APPROVED
    assert spy.call_count == 0


def test_reject_role_request_notify_false_suppresses_completion(
    db: Db, role_group: RoleGroup, okta_group: OktaGroup, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add_all([role_group, okta_group, user])
    db.session.commit()
    role_request = _pending_role_request(db, user, role_group, okta_group)

    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_role_request_completed")

    RejectRoleRequest(role_request=role_request, current_user_id=_access_owner(db), notify=False).execute()

    assert db.session.get(RoleRequest, role_request.id).status == AccessRequestStatus.REJECTED
    assert spy.call_count == 0


def test_approve_group_request_notify_false_suppresses_completion(
    db: Db, user: OktaUser, mocker: MockerFixture
) -> None:
    db.session.add(user)
    db.session.commit()
    group_request = _pending_group_request(db, user)

    mocker.patch.object(okta, "create_group", side_effect=lambda name, desc: Group({"id": "00gNOTIFYGATEGRP001"}))
    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_group_request_completed")

    ApproveGroupRequest(group_request=group_request, approver_user=_access_owner(db), notify=False).execute()

    assert db.session.get(GroupRequest, group_request.id).status == AccessRequestStatus.APPROVED
    assert spy.call_count == 0


def test_reject_group_request_notify_false_suppresses_completion(db: Db, user: OktaUser, mocker: MockerFixture) -> None:
    db.session.add(user)
    db.session.commit()
    group_request = _pending_group_request(db, user)

    hook = get_notification_hook()
    spy = mocker.patch.object(hook, "access_group_request_completed")

    RejectGroupRequest(group_request=group_request, current_user_id=_access_owner(db), notify=False).execute()

    assert db.session.get(GroupRequest, group_request.id).status == AccessRequestStatus.REJECTED
    assert spy.call_count == 0
