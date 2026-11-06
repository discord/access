"""Group requests submitted by service principals."""

from typing import Any

import pytest
from faker import Faker
from httpx import AsyncClient
from okta.models import Group
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequestStatus,
    App,
    GroupRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    ServicePrincipal,
    ServicePrincipalOwner,
)
from api.operations import ApproveGroupRequest, CreateGroupRequest
from api.plugins import ConditionalAccessResponse, get_notification_hook
from api.services import okta
from tests.factories import AppFactory, OktaGroupFactory, OktaUserFactory, RoleGroupFactory, ServicePrincipalFactory

GR_URL = "/api/group-requests"


@pytest.fixture(autouse=True)
def _okta(mocker: MockerFixture, faker: Faker) -> None:  # type: ignore[type-arg]
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group.from_dict({"id": faker.pystr(20, 20)})
    )
    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "add_user_to_group")


@pytest.fixture
def notifications(mocker: MockerFixture) -> dict[str, Any]:
    hook = get_notification_hook()
    return {
        "created": mocker.patch.object(hook, "access_group_request_created"),
        "completed": mocker.patch.object(hook, "access_group_request_completed"),
    }


async def _principal(db: Db) -> tuple[str, str]:
    """A registered service principal and one of its owners, as (principal id, owner id)."""
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build(display_name="Deploy bot")
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    return sp.id, owner.id


async def _admin_id(db: Db) -> str:
    return (
        await db.session.scalars(select(OktaUser.id).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).one()


def _body(name: str, **kwargs: Any) -> dict[str, Any]:
    return {"requested_group_name": name, "requested_group_type": "okta_group", "request_reason": "bot", **kwargs}


async def test_service_principal_creates_group_request(
    db: Db, client: AsyncClient, mock_user: Any, notifications: dict[str, Any]
) -> None:
    sp_id, owner_id = await _principal(db)
    mock_user(sp_id)

    rep = await client.post(GR_URL, json=_body("Bot-Group", requested_owner_user_ids=[owner_id]))
    assert rep.status_code == 201, rep.text
    data = rep.json()
    assert data["status"] == AccessRequestStatus.PENDING
    assert data["requester"] is None
    assert data["requester_service_principal"]["id"] == sp_id
    assert data["requester_service_principal"]["display_name"] == "Deploy bot"
    assert data["requester_service_principal"]["owners"][0]["user_id"] == owner_id
    assert data["requested_owner_user_ids"] == [owner_id]

    # Approvers hear about it like any other request.
    assert notifications["created"].call_count == 1
    assert notifications["created"].call_args.kwargs["requester"].id == sp_id


async def test_service_principal_request_must_name_owner(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, _ = await _principal(db)
    mock_user(sp_id)

    rep = await client.post(GR_URL, json=_body("Ownerless"))
    assert rep.status_code == 400
    assert "must name at least one owner" in rep.json()["detail"]

    role = await RoleGroupFactory.create_async()
    rep = await client.post(GR_URL, json=_body("Owned-By-Role", requested_owner_role_ids=[role.id]))
    assert rep.status_code == 201


async def test_unknown_caller_cannot_create_group_request(db: Db, client: AsyncClient, mock_user: Any) -> None:
    mock_user("unregistered.access")
    rep = await client.post(GR_URL, json=_body("Nope"))
    assert rep.status_code == 403

    sp_id, owner_id = await _principal(db)
    sp = await db.session.get(ServicePrincipal, sp_id)
    assert sp is not None
    sp.deleted_at = sp.created_at
    await db.session.commit()
    mock_user(sp_id)
    rep = await client.post(GR_URL, json=_body("Deleted", requested_owner_user_ids=[owner_id]))
    assert rep.status_code == 403


async def test_admin_approval_of_service_principal_request(
    db: Db, client: AsyncClient, mock_user: Any, notifications: dict[str, Any]
) -> None:
    sp_id, owner_id = await _principal(db)
    admin_id = await _admin_id(db)
    mock_user(sp_id)
    rep = await client.post(GR_URL, json=_body("Approved-Bot", requested_owner_user_ids=[owner_id]))
    gr_id = rep.json()["id"]

    mock_user(admin_id)
    rep = await client.put(f"{GR_URL}/{gr_id}", json={"approved": True, "reason": "ok"})
    assert rep.status_code == 200, rep.text
    group_id = rep.json()["approved_group_id"]

    group = await db.session.get(OktaGroup, group_id)
    assert group is not None
    assert group.created_by_service_principal_id == sp_id
    owners = (
        await db.session.scalars(
            select(OktaUserGroupMember.user_id)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.is_owner.is_(True))
        )
    ).all()
    assert owners == [owner_id]
    # No requester-facing notification for a service principal.
    assert notifications["completed"].call_count == 0


async def test_human_request_records_no_service_principal(db: Db, client: AsyncClient) -> None:
    rep = await client.post(GR_URL, json=_body("Human-Group"))
    gr_id = rep.json()["id"]
    gr = await db.session.get(GroupRequest, gr_id)
    assert gr is not None
    assert gr.requester_service_principal_id is None

    # The default test user is an Access admin, who can't approve their own request; a
    # plugin approval (no approver) stands in.
    await ApproveGroupRequest(group_request=gr_id).execute()
    await db.session.refresh(gr)
    assert gr.approved_group_id is not None
    group = await db.session.get(OktaGroup, gr.approved_group_id)
    assert group is not None
    assert group.created_by_service_principal_id is None


@pytest.mark.parametrize("approved", [True, False])
async def test_conditional_access_decides_service_principal_request(
    db: Db, mocker: MockerFixture, notifications: dict[str, Any], approved: bool
) -> None:
    sp_id, owner_id = await _principal(db)
    app_obj: App = await AppFactory.create_async()
    evaluate = mocker.patch(
        "api.operations.create_group_request.evaluate_conditional_access",
        return_value=[ConditionalAccessResponse(approved=approved, reason="rule", allow_named_grants=True)],
    )

    gr = await CreateGroupRequest(
        requester_user=sp_id,
        requested_group_name=f"App-{app_obj.name}-Stack",
        requested_group_type="app_group",
        requested_app_id=app_obj.id,
        requested_owner_user_ids=[owner_id],
    ).execute()
    assert gr is not None
    assert isinstance(evaluate.call_args.kwargs["requester"], ServicePrincipal)
    await db.session.refresh(gr)
    if approved:
        assert gr.status == AccessRequestStatus.APPROVED
        assert gr.approved_group_id is not None
        group = await db.session.get(OktaGroup, gr.approved_group_id)
        assert group is not None
        assert group.created_by_service_principal_id == sp_id
    else:
        assert gr.status == AccessRequestStatus.REJECTED
    assert notifications["created"].call_count == 0
    assert notifications["completed"].call_count == 0


async def test_service_principal_retry_replaces_pending_request(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, owner_id = await _principal(db)
    mock_user(sp_id)

    first = (await client.post(GR_URL, json=_body("Retry", requested_owner_user_ids=[owner_id]))).json()["id"]
    second = (await client.post(GR_URL, json=_body("Retry", requested_owner_user_ids=[owner_id]))).json()["id"]
    assert first != second
    assert (await client.get(f"{GR_URL}/{first}")).json()["status"] == AccessRequestStatus.REJECTED
    assert (await client.get(f"{GR_URL}/{second}")).json()["status"] == AccessRequestStatus.PENDING


async def test_service_principal_can_cancel_but_not_approve(
    db: Db, client: AsyncClient, mock_user: Any, notifications: dict[str, Any]
) -> None:
    sp_id, owner_id = await _principal(db)
    other_sp_id, _ = await _principal(db)
    mock_user(sp_id)
    own = (await client.post(GR_URL, json=_body("Own", requested_owner_user_ids=[owner_id]))).json()["id"]
    mock_user(other_sp_id)
    theirs = (await client.post(GR_URL, json=_body("Theirs", requested_owner_user_ids=[owner_id]))).json()["id"]

    mock_user(sp_id)
    assert (await client.put(f"{GR_URL}/{own}", json={"approved": True})).status_code == 403
    assert (await client.put(f"{GR_URL}/{theirs}", json={"approved": True})).status_code == 403
    assert (await client.put(f"{GR_URL}/{theirs}", json={"approved": False})).status_code == 403

    rep = await client.put(f"{GR_URL}/{own}", json={"approved": False, "reason": "not needed"})
    assert rep.status_code == 200, rep.text
    assert rep.json()["status"] == AccessRequestStatus.REJECTED
    assert rep.json()["resolver"] is None
    assert rep.json()["resolver_service_principal"]["id"] == sp_id
    assert notifications["completed"].call_count == 0


async def test_group_request_listing_includes_service_principals(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, owner_id = await _principal(db)
    admin_id = await _admin_id(db)
    mock_user(sp_id)
    gr_id = (await client.post(GR_URL, json=_body("Listed", requested_owner_user_ids=[owner_id]))).json()["id"]

    mine = (await client.get(GR_URL, params={"requester_user_id": "@me"})).json()["items"]
    assert [i["id"] for i in mine] == [gr_id]

    mock_user(admin_id)
    assigned = (await client.get(GR_URL, params={"assignee_user_id": "@me"})).json()["items"]
    assert gr_id in [i["id"] for i in assigned]
    by_name = (await client.get(GR_URL, params={"q": "deploy bot"})).json()["items"]
    assert [i["id"] for i in by_name] == [gr_id]
    by_requester = (await client.get(GR_URL, params={"requester_user_id": "Deploy bot"})).json()["items"]
    assert [i["id"] for i in by_requester] == [gr_id]


async def test_service_principal_cannot_request_or_manage_access(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, owner_id = await _principal(db)
    group = await OktaGroupFactory.create_async()
    role = await RoleGroupFactory.create_async()
    app_obj = await AppFactory.create_async()
    group_id, role_id, app_id, group_name = group.id, role.id, app_obj.id, group.name
    mock_user(sp_id)

    rep = await client.post("/api/requests", json={"group_id": group_id, "group_owner": False, "reason": "x"})
    assert rep.status_code == 403
    rep = await client.post("/api/role-requests", json={"role_id": role_id, "group_id": group_id, "reason": "x"})
    assert rep.status_code == 403

    rep = await client.put(f"/api/groups/{group_id}", json={"name": group_name, "type": "okta_group"})
    assert rep.status_code == 403
    rep = await client.put(
        f"/api/groups/{group_id}/members",
        json={"members_to_add": [owner_id], "members_to_remove": [], "owners_to_add": [], "owners_to_remove": []},
    )
    assert rep.status_code == 403
    rep = await client.put(f"/api/apps/{app_id}", json={"name": "Renamed"})
    assert rep.status_code == 403


async def test_deleting_principal_rejects_its_pending_requests(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, owner_id = await _principal(db)
    admin_id = await _admin_id(db)
    mock_user(sp_id)
    gr_id = (await client.post(GR_URL, json=_body("Stranded", requested_owner_user_ids=[owner_id]))).json()["id"]

    mock_user(admin_id)
    assert (await client.delete(f"/api/service-principals/{sp_id}")).status_code == 200
    assert (await client.get(f"{GR_URL}/{gr_id}")).json()["status"] == AccessRequestStatus.REJECTED


async def test_plugin_approval_needs_named_grants_opt_in(db: Db, mocker: MockerFixture) -> None:
    sp_id, owner_id = await _principal(db)
    mocker.patch(
        "api.operations.create_group_request.evaluate_conditional_access",
        return_value=[ConditionalAccessResponse(approved=True, reason="legacy rule")],
    )
    gr = await CreateGroupRequest(
        requester_user=sp_id,
        requested_group_name="Needs-Opt-In",
        requested_group_type="okta_group",
        requested_owner_user_ids=[owner_id],
    ).execute()
    assert gr is not None
    await db.session.refresh(gr)
    # A service principal's request always names someone else as owner.
    assert gr.status == AccessRequestStatus.PENDING


async def test_request_audit_names_requester_service_principal(
    db: Db, client: AsyncClient, mock_user: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import json
    import logging

    sp_id, owner_id = await _principal(db)
    mock_user(sp_id)
    with caplog.at_level(logging.INFO, logger="access.audit"):
        await client.post(GR_URL, json=_body("Audited", requested_owner_user_ids=[owner_id]))
    entry = next(
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "access.audit" and "GROUP_REQUEST_CREATE" in r.getMessage()
    )
    assert entry["current_user_id"] == sp_id
    assert entry["requester_service_principal"]["id"] == sp_id
    assert "service_principal" not in entry
