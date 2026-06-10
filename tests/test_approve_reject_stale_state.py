"""Spec tests for the approve/reject stale-state bug.

Today the approve/reject operations *silently no-op* when they hit a guard
(already-resolved request, deleted requester/target, unmanaged group): they
return the request object unchanged with no error and, for several guards, no
status flip. This both confuses callers and (under concurrency) lets a stale
approval slip a second grant past the router's pre-check.

These tests encode the contract for the fix. Operations raise `AccessException`
subclasses (decoupled from FastAPI; mapped to HTTP by api.exception_handlers):
  - already-resolved approve/reject  -> ConflictError (409)
  - deleted requester / deleted-or-unmanaged target -> 410 / 400
  - a stale approval (request resolved between load and execute) must conflict
    and must NOT create a grant.

The true two-connection duplicate-row race can only be reproduced against
Postgres; the single-connection sqlite harness shares one connection, so these
simulate the stale/already-resolved state deterministically instead.
"""

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from okta.models import Group
from pytest_mock import MockerFixture
from sqlalchemy import func, or_
from starlette.requests import Request

from api.config import settings
from api.exception_handlers import access_exception_handler
from api.exceptions import AccessException, ConflictError, InvalidRequestError, ResourceGoneError
from api.extensions import Db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
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
)
from api.services import okta


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # The approve path pushes membership to Okta via ModifyGroupUsers; stub it
    # so these tests never hit the network. patch.object auto-detects the async
    # functions and substitutes AsyncMocks.
    mocker.patch.object(okta, "async_add_user_to_group")
    mocker.patch.object(okta, "async_add_owner_to_group")
    # Group-request approval creates a group in Okta; stub it.
    mocker.patch.object(okta, "create_group", side_effect=lambda name, desc: Group({"id": uuid.uuid4().hex}))


def _access_owner(db: Db) -> OktaUser:
    owner = db.session.query(OktaUser).filter(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL).first()
    assert owner is not None
    return owner


def _active_direct_member_count(db: Db, *, user_id: str, group_id: str, is_owner: bool = False) -> int:
    return (
        db.session.query(OktaUserGroupMember)
        .filter(OktaUserGroupMember.user_id == user_id)
        .filter(OktaUserGroupMember.group_id == group_id)
        .filter(OktaUserGroupMember.is_owner.is_(is_owner))
        .filter(OktaUserGroupMember.role_group_map_id.is_(None))
        .filter(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
        .count()
    )


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------


def test_approve_already_resolved_access_request_conflicts(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    owner = _access_owner(db)

    request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()
    assert _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 1

    # A second approval of the now-APPROVED request must conflict, not no-op.
    with pytest.raises(AccessException) as exc:
        ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="again").execute()
    assert exc.value.status_code == 409

    # And it must not have created a duplicate grant.
    assert _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 1


def test_reject_already_resolved_access_request_conflicts(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    owner = _access_owner(db)

    request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()

    # Rejecting an already-approved request must conflict, not silently no-op.
    with pytest.raises(AccessException) as exc:
        RejectAccessRequest(
            access_request=request.id,
            rejection_reason="too late",
            current_user_id=owner.id,
        ).execute()
    assert exc.value.status_code == 409


def test_approve_access_request_with_deleted_requester_errors(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    owner = _access_owner(db)

    request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    # Requester is soft-deleted before the approval is processed.
    user.deleted_at = func.now()
    db.session.commit()

    with pytest.raises(AccessException) as exc:
        ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()
    assert exc.value.status_code in (400, 410)

    # The error is surfaced (not a silent success) and no grant is created.
    # The request stays PENDING — the approver gets a clear 4xx rather than the
    # operation quietly resolving a request it can't fulfil.
    assert _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 0
    refreshed = db.session.get(AccessRequest, request.id)
    assert refreshed is not None
    assert refreshed.status == AccessRequestStatus.PENDING


def test_approve_access_request_with_unmanaged_group_errors(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    owner = _access_owner(db)

    request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    # Target group becomes externally managed before the approval is processed.
    okta_group.is_managed = False
    db.session.commit()

    with pytest.raises(AccessException) as exc:
        ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()
    assert exc.value.status_code in (400, 410)
    assert _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 0


def test_stale_resolution_then_approve_conflicts_without_granting(
    db: Db, user: OktaUser, okta_group: OktaGroup
) -> None:
    """Approver loads a PENDING request; another actor resolves it; the
    approver's execute() must conflict (409) and grant nothing."""
    db.session.add(user)
    db.session.add(okta_group)
    db.session.commit()
    owner = _access_owner(db)

    request = CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    # Operation loads the request while it is still PENDING.
    op = ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok")

    # A concurrent actor resolves the request out from under it (direct DB flip
    # mirrors the row another approver's transaction would have committed).
    db.session.query(AccessRequest).filter(AccessRequest.id == request.id).update(
        {AccessRequest.status: AccessRequestStatus.APPROVED, AccessRequest.resolved_at: func.now()},
        synchronize_session=False,
    )
    db.session.commit()

    with pytest.raises(AccessException) as exc:
        op.execute()
    assert exc.value.status_code == 409

    # The stale approver must not have slipped in a grant.
    assert _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 0


# ---------------------------------------------------------------------------
# Role requests
# ---------------------------------------------------------------------------


def test_approve_already_resolved_role_request_conflicts(
    db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    db.session.commit()
    owner = _access_owner(db)

    # Requester must own the role to file a role request for it.
    ModifyGroupUsers(group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False).execute()

    request = CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="role needs this group",
    ).execute()
    assert request is not None

    ApproveRoleRequest(role_request=request.id, approver_user=owner, approval_reason="ok").execute()

    active_maps = (
        db.session.query(RoleGroupMap)
        .filter(RoleGroupMap.role_group_id == role_group.id)
        .filter(RoleGroupMap.group_id == okta_group.id)
        .filter(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
        .count()
    )
    assert active_maps == 1

    with pytest.raises(AccessException) as exc:
        ApproveRoleRequest(role_request=request.id, approver_user=owner, approval_reason="again").execute()
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Group requests
# ---------------------------------------------------------------------------


def test_approve_already_resolved_group_request_conflicts(db: Db, user: OktaUser) -> None:
    db.session.add(user)
    db.session.commit()
    owner = _access_owner(db)

    request = CreateGroupRequest(
        requester_user=user,
        requested_group_name="Test Group",
        requested_group_description="Test Description",
        requested_group_type="okta_group",
        request_reason="need a group",
    ).execute()
    assert request is not None

    ApproveGroupRequest(group_request=request.id, approver_user=owner, approval_reason="ok").execute()

    with pytest.raises(AccessException) as exc:
        ApproveGroupRequest(group_request=request.id, approver_user=owner, approval_reason="again").execute()
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# AccessException -> HTTP mapping
# ---------------------------------------------------------------------------


def test_access_exception_handler_maps_status_and_envelope() -> None:
    req = Request({"type": "http", "method": "GET", "path": "/api/x", "headers": [], "query_string": b""})
    cases = [
        (ConflictError("nope"), 409),
        (ResourceGoneError("gone"), 410),
        (InvalidRequestError("bad"), 400),
        (AccessException("teapot", status_code=418), 418),  # constructor override
    ]
    for exc, expected in cases:
        resp = asyncio.run(access_exception_handler(req, exc))
        assert resp.status_code == expected
        assert resp.media_type == "application/problem+json"


def test_access_exception_handler_is_registered(app: FastAPI) -> None:
    # Guards against the handler silently falling through to the 500 catch-all.
    assert app.exception_handlers.get(AccessException) is access_exception_handler
