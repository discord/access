"""Requests to delete groups and apps."""

from typing import Any

import pytest
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    ServicePrincipal,
    ServicePrincipalOwner,
)
from api.operations import CreateDeletionRequest
from api.plugins import ConditionalAccessResponse, get_notification_hook
from api.services import okta
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserFactory,
    OktaUserGroupMemberFactory,
    ServicePrincipalFactory,
)

DR_URL = "/api/deletion-requests"


@pytest.fixture(autouse=True)
def _okta(mocker: MockerFixture) -> None:
    mocker.patch.object(okta, "delete_group")


@pytest.fixture
def notifications(mocker: MockerFixture) -> dict[str, Any]:
    hook = get_notification_hook()
    return {
        "created": mocker.patch.object(hook, "access_deletion_request_created"),
        "completed": mocker.patch.object(hook, "access_deletion_request_completed"),
    }


async def _admin_id(db: Db) -> str:
    return (
        await db.session.scalars(select(OktaUser.id).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).one()


async def _owned_group(db: Db) -> tuple[str, str]:
    """A plain group with one direct owner, as (group id, owner id)."""
    group = await OktaGroupFactory.create_async()
    owner = await OktaUserFactory.create_async()
    await OktaUserGroupMemberFactory.create_async(user_id=owner.id, group_id=group.id, is_owner=True)
    return group.id, owner.id


async def _app_with_owner(db: Db) -> tuple[str, str, str]:
    """An app with an owner, as (app id, app name, owner id)."""
    app = await AppFactory.create_async()
    owners_group = await AppGroupFactory.create_async(
        app_id=app.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        f"{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
    )
    owner = await OktaUserFactory.create_async()
    await OktaUserGroupMemberFactory.create_async(user_id=owner.id, group_id=owners_group.id, is_owner=True)
    return app.id, app.name, owner.id


async def _deleted(db: Db, model: type[OktaGroup] | type[App], id_: str) -> bool:
    db.session.expire_all()
    row = await db.session.get(model, id_)
    return row is not None and row.deleted_at is not None


async def test_group_owner_approves_group_deletion(
    db: Db, client: AsyncClient, mock_user: Any, notifications: dict[str, Any]
) -> None:
    group_id, owner_id = await _owned_group(db)
    requester = await OktaUserFactory.create_async()
    requester_id = requester.id

    mock_user(requester_id)
    rep = await client.post(DR_URL, json={"group_id": group_id, "request_reason": "unused"})
    assert rep.status_code == 201, rep.text
    dr = rep.json()
    assert dr["status"] == AccessRequestStatus.PENDING
    assert dr["group"]["id"] == group_id
    # Routed to the group's owner, not to Access admins.
    assert [a.id for a in notifications["created"].call_args.kwargs["approvers"]] == [owner_id]

    assert (await client.put(f"{DR_URL}/{dr['id']}", json={"approved": True})).status_code == 403
    mock_user(await OktaUserFactory.create_async())
    assert (await client.put(f"{DR_URL}/{dr['id']}", json={"approved": True})).status_code == 403

    mock_user(owner_id)
    rep = await client.put(f"{DR_URL}/{dr['id']}", json={"approved": True, "reason": "ok"})
    assert rep.status_code == 200, rep.text
    assert rep.json()["status"] == AccessRequestStatus.APPROVED
    assert rep.json()["resolver"]["id"] == owner_id
    assert await _deleted(db, OktaGroup, group_id)
    assert notifications["completed"].call_count == 1


async def test_app_group_deletion_routes_to_app_owner(
    db: Db, client: AsyncClient, notifications: dict[str, Any]
) -> None:
    app_id, app_name, app_owner_id = await _app_with_owner(db)
    group = await AppGroupFactory.create_async(app_id=app_id, is_owner=False, name=f"App-{app_name}-Ownerless")

    rep = await client.post(DR_URL, json={"group_id": group.id})
    assert rep.status_code == 201
    assert [a.id for a in notifications["created"].call_args.kwargs["approvers"]] == [app_owner_id]


async def test_app_owner_approves_app_deletion(db: Db, client: AsyncClient, mock_user: Any) -> None:
    app_id, _, app_owner_id = await _app_with_owner(db)

    rep = await client.post(DR_URL, json={"app_id": app_id})
    assert rep.status_code == 201
    mock_user(app_owner_id)
    rep = await client.put(f"{DR_URL}/{rep.json()['id']}", json={"approved": True})
    assert rep.status_code == 200, rep.text
    assert await _deleted(db, App, app_id)


async def test_target_validation(db: Db, client: AsyncClient) -> None:
    access_app = (await db.session.scalars(select(App).where(App.name == App.ACCESS_APP_RESERVED_NAME))).one()
    owners_group = (
        await db.session.scalars(select(AppGroup).where(AppGroup.app_id == access_app.id).where(AppGroup.is_owner))
    ).one()
    unmanaged = await OktaGroupFactory.create_async(is_managed=False)
    access_app_id, owners_group_id, unmanaged_id = access_app.id, owners_group.id, unmanaged.id

    cases: list[tuple[dict[str, Any], int]] = [
        ({"app_id": access_app_id}, 400),
        ({"group_id": owners_group_id}, 400),
        ({"group_id": unmanaged_id}, 400),
        ({"group_id": "missing"}, 410),
        ({}, 400),
        ({"group_id": unmanaged_id, "app_id": access_app_id}, 400),
    ]
    for body, status in cases:
        assert (await client.post(DR_URL, json=body)).status_code == status, body


async def test_approval_after_target_deleted(db: Db, client: AsyncClient, mock_user: Any) -> None:
    group_id, owner_id = await _owned_group(db)
    requester = await OktaUserFactory.create_async()
    dr = await CreateDeletionRequest(requester=requester, group_id=group_id).execute()
    dr_id = dr.id
    group = await db.session.get(OktaGroup, group_id)
    assert group is not None
    group.deleted_at = group.created_at
    await db.session.commit()

    mock_user(owner_id)
    assert (await client.put(f"{DR_URL}/{dr_id}", json={"approved": True})).status_code == 410
    # It can still be rejected.
    rep = await client.put(f"{DR_URL}/{dr_id}", json={"approved": False})
    assert rep.status_code == 200
    assert rep.json()["status"] == AccessRequestStatus.REJECTED


async def test_requester_withdraws_and_retry_replaces(db: Db, client: AsyncClient, mock_user: Any) -> None:
    group_id, _ = await _owned_group(db)
    requester = await OktaUserFactory.create_async()
    mock_user(requester.id)

    first = (await client.post(DR_URL, json={"group_id": group_id})).json()["id"]
    second = (await client.post(DR_URL, json={"group_id": group_id})).json()["id"]
    assert (await client.get(f"{DR_URL}/{first}")).json()["status"] == AccessRequestStatus.REJECTED
    mine = (await client.get(DR_URL, params={"requester_user_id": "@me", "status": "PENDING"})).json()["items"]
    assert [i["id"] for i in mine] == [second]

    rep = await client.put(f"{DR_URL}/{second}", json={"approved": False})
    assert rep.status_code == 200
    assert rep.json()["status"] == AccessRequestStatus.REJECTED
    assert not await _deleted(db, OktaGroup, group_id)


@pytest.mark.parametrize("created_by_requester", [True, False])
async def test_plugin_can_limit_service_to_what_it_created(
    db: Db, mocker: MockerFixture, notifications: dict[str, Any], created_by_requester: bool
) -> None:
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build()
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    sp_id = sp.id
    group = await OktaGroupFactory.create_async(created_by_service_principal_id=sp_id if created_by_requester else None)
    group_id = group.id

    async def rule(hook: Any, **kwargs: Any) -> list[ConditionalAccessResponse | None]:
        requester, target = kwargs["requester"], kwargs["group"]
        if isinstance(requester, ServicePrincipal) and target.created_by_service_principal_id == requester.id:
            return [ConditionalAccessResponse(approved=True, reason="created it")]
        return [None]

    mocker.patch("api.operations.create_deletion_request.evaluate_conditional_access", side_effect=rule)

    dr = await CreateDeletionRequest(requester=sp_id, group_id=group_id).execute()
    await db.session.refresh(dr)
    if created_by_requester:
        assert dr.status == AccessRequestStatus.APPROVED
        assert dr.resolver_user_id is None
        assert await _deleted(db, OktaGroup, group_id)
        assert notifications["created"].call_count == 0
    else:
        assert dr.status == AccessRequestStatus.PENDING
        assert notifications["created"].call_count == 1
    assert notifications["completed"].call_count == 0


async def test_service_principal_cannot_approve_deletions(db: Db, client: AsyncClient, mock_user: Any) -> None:
    group_id, _ = await _owned_group(db)
    requester = await OktaUserFactory.create_async()
    dr = await CreateDeletionRequest(requester=requester, group_id=group_id).execute()
    dr_id = dr.id
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build()
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()

    mock_user(sp.id)
    assert (await client.put(f"{DR_URL}/{dr_id}", json={"approved": True})).status_code == 403
    assert (await client.put(f"{DR_URL}/{dr_id}", json={"approved": False})).status_code == 403


async def test_admin_can_approve_any_deletion(db: Db, client: AsyncClient) -> None:
    group_id, _ = await _owned_group(db)
    requester = await OktaUserFactory.create_async()
    dr = await CreateDeletionRequest(requester=requester, group_id=group_id).execute()
    rep = await client.put(f"{DR_URL}/{dr.id}", json={"approved": True})
    assert rep.status_code == 200
    assert rep.json()["resolver"]["id"] == await _admin_id(db)


async def test_deleting_a_target_closes_its_other_requests(db: Db, client: AsyncClient, mock_user: Any) -> None:
    group_id, owner_id = await _owned_group(db)
    first = await CreateDeletionRequest(requester=await OktaUserFactory.create_async(), group_id=group_id).execute()
    second = await CreateDeletionRequest(requester=await OktaUserFactory.create_async(), group_id=group_id).execute()
    first_id, second_id = first.id, second.id

    mock_user(owner_id)
    rep = await client.put(f"{DR_URL}/{first_id}", json={"approved": True})
    assert rep.json()["status"] == AccessRequestStatus.APPROVED
    rep = await client.get(f"{DR_URL}/{second_id}")
    assert rep.json()["status"] == AccessRequestStatus.REJECTED
    assert rep.json()["resolution_reason"] == "Closed because the group was deleted"


async def test_deleting_an_app_directly_closes_requests_for_it_and_its_groups(
    db: Db, client: AsyncClient, mock_user: Any
) -> None:
    app_id, app_name, app_owner_id = await _app_with_owner(db)
    group = await AppGroupFactory.create_async(app_id=app_id, is_owner=False, name=f"App-{app_name}-Child")
    requester = await OktaUserFactory.create_async()
    for_app = await CreateDeletionRequest(requester=requester, app_id=app_id).execute()
    for_group = await CreateDeletionRequest(requester=requester, group_id=group.id).execute()
    ids = [for_app.id, for_group.id]

    mock_user(app_owner_id)
    assert (await client.delete(f"/api/apps/{app_id}")).status_code == 200
    for dr_id in ids:
        assert (await client.get(f"{DR_URL}/{dr_id}")).json()["status"] == AccessRequestStatus.REJECTED


async def test_deleting_the_requester_closes_its_requests(db: Db, client: AsyncClient, mock_user: Any) -> None:
    group_id, _ = await _owned_group(db)
    user = await OktaUserFactory.create_async()
    user_request = await CreateDeletionRequest(requester=user, group_id=group_id).execute()
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build()
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    sp_id = sp.id
    sp_request = await CreateDeletionRequest(requester=sp_id, group_id=group_id).execute()
    user_id, ids = user.id, [user_request.id, sp_request.id]

    from api.operations import DeleteServicePrincipal, DeleteUser

    await DeleteUser(user=user_id, sync_to_okta=False).execute()
    await DeleteServicePrincipal(service_principal=sp_id).execute()
    for dr_id in ids:
        assert (await client.get(f"{DR_URL}/{dr_id}")).json()["status"] == AccessRequestStatus.REJECTED


async def test_service_principal_withdrawal_is_recorded(db: Db, client: AsyncClient, mock_user: Any) -> None:
    group_id, _ = await _owned_group(db)
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build(display_name="Cleanup bot")
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    sp_id = sp.id
    mock_user(sp_id)

    dr_id = (await client.post(DR_URL, json={"group_id": group_id})).json()["id"]
    rep = await client.put(f"{DR_URL}/{dr_id}", json={"approved": False})
    assert rep.json()["resolver"] is None
    assert rep.json()["resolver_service_principal"]["id"] == sp_id

    found = (await client.get(DR_URL, params={"requester_user_id": "Cleanup bot"})).json()["items"]
    assert [i["id"] for i in found] == [dr_id]
    found = (await client.get(DR_URL, params={"q": "cleanup"})).json()["items"]
    assert [i["id"] for i in found] == [dr_id]


async def test_audit_names_requester_service_principal(
    db: Db, client: AsyncClient, mock_user: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import json
    import logging

    group_id, _ = await _owned_group(db)
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build()
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    sp_id = sp.id
    mock_user(sp_id)

    with caplog.at_level(logging.INFO, logger="access.audit"):
        await client.post(DR_URL, json={"group_id": group_id})
    entry = next(
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "access.audit" and "DELETION_REQUEST_CREATE" in r.getMessage()
    )
    assert entry["requester_service_principal"]["id"] == sp_id
    assert "service_principal" not in entry
