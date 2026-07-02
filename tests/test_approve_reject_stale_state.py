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

import uuid

import pytest
from fastapi import FastAPI
from okta.models import Group
from pytest_mock import MockerFixture
from sqlalchemy import func, or_, select, update
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
from tests.helpers import db_count


@pytest.fixture(autouse=True)
def _mock_okta(mocker: MockerFixture) -> None:
    # The approve path pushes membership to Okta via ModifyGroupUsers; stub it
    # so these tests never hit the network. patch.object auto-detects the async
    # functions and substitutes AsyncMocks.
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")
    # Group-request approval creates a group in Okta; stub it.
    mocker.patch.object(okta, "create_group", side_effect=lambda name, desc: Group({"id": uuid.uuid4().hex}))


async def _access_owner(db: Db) -> OktaUser:
    owner = (
        await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).first()
    assert owner is not None
    return owner


async def _active_direct_member_count(db: Db, *, user_id: str, group_id: str, is_owner: bool = False) -> int:
    return await db_count(
        db.session,
        select(OktaUserGroupMember)
        .where(OktaUserGroupMember.user_id == user_id)
        .where(OktaUserGroupMember.group_id == group_id)
        .where(OktaUserGroupMember.is_owner.is_(is_owner))
        .where(OktaUserGroupMember.role_group_map_id.is_(None))
        .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now())),
    )


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------


async def test_approve_already_resolved_access_request_conflicts(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    owner = await _access_owner(db)

    request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    await ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()
    assert await _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 1

    # A second approval of the now-APPROVED request must conflict, not no-op.
    with pytest.raises(AccessException) as exc:
        await ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="again").execute()
    assert exc.value.status_code == 409

    # And it must not have created a duplicate grant.
    assert await _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 1


async def test_reject_already_resolved_access_request_conflicts(db: Db, user: OktaUser, okta_group: OktaGroup) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    owner = await _access_owner(db)

    request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    await ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()

    # Rejecting an already-approved request must conflict, not silently no-op.
    with pytest.raises(AccessException) as exc:
        await RejectAccessRequest(
            access_request=request.id,
            rejection_reason="too late",
            current_user_id=owner.id,
        ).execute()
    assert exc.value.status_code == 409


async def test_approve_access_request_with_deleted_requester_errors(
    db: Db, user: OktaUser, okta_group: OktaGroup
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    owner = await _access_owner(db)

    request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    # Requester is soft-deleted before the approval is processed.
    user.deleted_at = func.now()
    await db.session.commit()
    # The server-generated deleted_at is expired by the flush; reload it on
    # the async session so the operation's plain attribute read doesn't
    # trigger a sync lazy-load.
    await db.session.refresh(user)

    with pytest.raises(AccessException) as exc:
        await ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()
    assert exc.value.status_code in (400, 410)

    # The error is surfaced (not a silent success) and no grant is created.
    # The request stays PENDING — the approver gets a clear 4xx rather than the
    # operation quietly resolving a request it can't fulfil.
    assert await _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 0
    refreshed = await db.session.get(AccessRequest, request.id)
    assert refreshed is not None
    assert refreshed.status == AccessRequestStatus.PENDING


async def test_approve_access_request_with_unmanaged_group_errors(
    db: Db, user: OktaUser, okta_group: OktaGroup
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    owner = await _access_owner(db)

    request = await CreateAccessRequest(
        requester_user=user,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="need access",
    ).execute()
    assert request is not None

    # Target group becomes externally managed before the approval is processed.
    okta_group.is_managed = False
    await db.session.commit()

    with pytest.raises(AccessException) as exc:
        await ApproveAccessRequest(access_request=request.id, approver_user=owner, approval_reason="ok").execute()
    assert exc.value.status_code in (400, 410)
    assert await _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 0


async def test_stale_resolution_then_approve_conflicts_without_granting(
    db: Db, user: OktaUser, okta_group: OktaGroup
) -> None:
    """Approver loads a PENDING request; another actor resolves it; the
    approver's execute() must conflict (409) and grant nothing."""
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    owner = await _access_owner(db)

    request = await CreateAccessRequest(
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
    await db.session.execute(
        update(AccessRequest)
        .where(AccessRequest.id == request.id)
        .values(status=AccessRequestStatus.APPROVED, resolved_at=func.now())
        .execution_options(synchronize_session=False)
    )
    await db.session.commit()

    # drop identity-map state staled by the ops above (expire_on_commit=False)
    db.session.expire_all()
    # expire_all also expired the objects this test still reads directly;
    # reload them on the async session so plain attribute access works.
    for obj in (owner, user, okta_group):
        await db.session.refresh(obj)

    with pytest.raises(AccessException) as exc:
        await op.execute()
    assert exc.value.status_code == 409

    # The stale approver must not have slipped in a grant.
    assert await _active_direct_member_count(db, user_id=user.id, group_id=okta_group.id) == 0


# ---------------------------------------------------------------------------
# Role requests
# ---------------------------------------------------------------------------


async def test_approve_already_resolved_role_request_conflicts(
    db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
) -> None:
    db.session.add(user)
    db.session.add(okta_group)
    db.session.add(role_group)
    await db.session.commit()
    owner = await _access_owner(db)

    # Requester must own the role to file a role request for it.
    await ModifyGroupUsers(
        group=role_group, members_to_add=[user.id], owners_to_add=[user.id], sync_to_okta=False
    ).execute()

    request = await CreateRoleRequest(
        requester_user=user,
        requester_role=role_group,
        requested_group=okta_group,
        request_ownership=False,
        request_reason="role needs this group",
    ).execute()
    assert request is not None

    await ApproveRoleRequest(role_request=request.id, approver_user=owner, approval_reason="ok").execute()

    active_maps = await db_count(
        db.session,
        select(RoleGroupMap)
        .where(RoleGroupMap.role_group_id == role_group.id)
        .where(RoleGroupMap.group_id == okta_group.id)
        .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now())),
    )
    assert active_maps == 1

    with pytest.raises(AccessException) as exc:
        await ApproveRoleRequest(role_request=request.id, approver_user=owner, approval_reason="again").execute()
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Group requests
# ---------------------------------------------------------------------------


async def test_approve_already_resolved_group_request_conflicts(db: Db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()
    owner = await _access_owner(db)

    request = await CreateGroupRequest(
        requester_user=user,
        requested_group_name="Test Group",
        requested_group_description="Test Description",
        requested_group_type="okta_group",
        request_reason="need a group",
    ).execute()
    assert request is not None

    await ApproveGroupRequest(group_request=request.id, approver_user=owner, approval_reason="ok").execute()

    with pytest.raises(AccessException) as exc:
        await ApproveGroupRequest(group_request=request.id, approver_user=owner, approval_reason="again").execute()
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# AccessException -> HTTP mapping
# ---------------------------------------------------------------------------


async def test_access_exception_handler_maps_status_and_envelope() -> None:
    req = Request({"type": "http", "method": "GET", "path": "/api/x", "headers": [], "query_string": b""})
    cases = [
        (ConflictError("nope"), 409),
        (ResourceGoneError("gone"), 410),
        (InvalidRequestError("bad"), 400),
        (AccessException("teapot", status_code=418), 418),  # constructor override
    ]
    for exc, expected in cases:
        resp = await access_exception_handler(req, exc)
        assert resp.status_code == expected
        assert resp.media_type == "application/problem+json"


def test_access_exception_handler_is_registered(app: FastAPI) -> None:
    # Guards against the handler silently falling through to the 500 catch-all.
    assert app.exception_handlers.get(AccessException) is access_exception_handler
