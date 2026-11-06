"""App requests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from faker import Faker
from httpx import AsyncClient
from okta.models import Group
from pytest_mock import MockerFixture
from sqlalchemy import func, or_, select

from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    AppRequest,
    OktaGroup,
    OktaUser,
    OktaUserGroupMember,
    RoleGroupMap,
    ServicePrincipal,
    ServicePrincipalOwner,
    Tag,
)
from api.operations import CreateAppRequest, DeleteUser
from api.plugins import ConditionalAccessResponse, get_notification_hook
from api.schemas.app_requests import AppRequestGroup
from api.services import okta
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserFactory,
    RoleGroupFactory,
    ServicePrincipalFactory,
    TagFactory,
)

AR_URL = "/api/app-requests"
DAY = timedelta(days=1)


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
        "created": mocker.patch.object(hook, "access_app_request_created"),
        "completed": mocker.patch.object(hook, "access_app_request_completed"),
    }


async def _admin_id(db: Db) -> str:
    return (
        await db.session.scalars(select(OktaUser.id).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))
    ).one()


async def _principal(db: Db) -> tuple[str, str]:
    owner = await OktaUserFactory.create_async()
    sp = ServicePrincipalFactory.build()
    sp.owners = [ServicePrincipalOwner(user_id=owner.id)]
    db.session.add(sp)
    await db.session.commit()
    return sp.id, owner.id


async def _grants(db: Db, group_id: str) -> dict[str, dict[str, datetime | None]]:
    users = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.role_group_map_id.is_(None))
            .where(or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now()))
        )
    ).all()
    roles = (
        await db.session.scalars(
            select(RoleGroupMap)
            .where(RoleGroupMap.group_id == group_id)
            .where(or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now()))
        )
    ).all()
    return {
        "owner_users": {m.user_id: m.ended_at for m in users if m.is_owner},
        "member_users": {m.user_id: m.ended_at for m in users if not m.is_owner},
        "owner_roles": {m.role_group_id: m.ended_at for m in roles if m.is_owner},
        "member_roles": {m.role_group_id: m.ended_at for m in roles if not m.is_owner},
    }


async def _group_id(db: Db, name: str) -> str:
    return (await db.session.scalars(select(AppGroup.id).where(AppGroup.name == name))).one()


def _close(actual: datetime | None, expected: datetime) -> bool:
    assert actual is not None
    return abs(actual.replace(tzinfo=UTC) - expected) < timedelta(minutes=5)


async def test_user_requests_and_admin_approves_app(
    db: Db, client: AsyncClient, mock_user: Any, notifications: dict[str, Any]
) -> None:
    requester = await OktaUserFactory.create_async()
    app_owner = await OktaUserFactory.create_async()
    owner_role = await RoleGroupFactory.create_async()
    deployer = await OktaUserFactory.create_async()
    deployer_role = await RoleGroupFactory.create_async()
    ids = {
        "requester": requester.id,
        "app_owner": app_owner.id,
        "owner_role": owner_role.id,
        "deployer": deployer.id,
        "deployer_role": deployer_role.id,
    }
    admin_id = await _admin_id(db)
    member_end = datetime.now(UTC) + 14 * DAY

    mock_user(ids["requester"])
    rep = await client.post(
        AR_URL,
        json={
            "requested_app_name": "Billing",
            "requested_app_description": "Billing service",
            "requested_owner_user_ids": [ids["app_owner"]],
            "requested_owner_role_ids": [ids["owner_role"]],
            "requested_app_groups": [
                {
                    "name": "App-Billing-Deployers",
                    "owner_role_ids": [ids["owner_role"]],
                    "member_user_ids": [ids["deployer"]],
                    "member_role_ids": [ids["deployer_role"]],
                }
            ],
            "requested_membership_ending_at": member_end.isoformat(),
            "request_reason": "new service",
        },
    )
    assert rep.status_code == 201, rep.text
    data = rep.json()
    assert data["status"] == AccessRequestStatus.PENDING
    assert data["requester"]["id"] == ids["requester"]
    assert {u["id"] for u in data["named_users"]} == {ids["app_owner"], ids["deployer"]}
    assert {r["id"] for r in data["named_roles"]} == {ids["owner_role"], ids["deployer_role"]}
    assert notifications["created"].call_count == 1
    assert {a.id for a in notifications["created"].call_args.kwargs["approvers"]} == {admin_id}

    # The requester can't approve it, and neither can a non-admin.
    assert (await client.put(f"{AR_URL}/{data['id']}", json={"approved": True})).status_code == 403
    mock_user(ids["app_owner"])
    assert (await client.put(f"{AR_URL}/{data['id']}", json={"approved": True})).status_code == 403

    mock_user(admin_id)
    rep = await client.put(f"{AR_URL}/{data['id']}", json={"approved": True, "reason": "ok"})
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert data["status"] == AccessRequestStatus.APPROVED
    assert data["resolver"]["id"] == admin_id

    app = await db.session.get(App, data["approved_app_id"])
    assert app is not None
    assert app.name == "Billing"
    assert app.created_by_service_principal_id is None

    owners_group = await _grants(db, await _group_id(db, "App-Billing-Owners"))
    assert set(owners_group["owner_users"]) == {ids["app_owner"]}
    assert set(owners_group["member_users"]) == {ids["app_owner"]}
    assert set(owners_group["owner_roles"]) == {ids["owner_role"]}
    assert set(owners_group["member_roles"]) == {ids["owner_role"]}

    deployers = await _grants(db, await _group_id(db, "App-Billing-Deployers"))
    assert set(deployers["owner_roles"]) == {ids["owner_role"]}
    assert _close(deployers["member_users"][ids["deployer"]], member_end)
    assert _close(deployers["member_roles"][ids["deployer_role"]], member_end)

    assert notifications["completed"].call_count == 1
    assert notifications["completed"].call_args.kwargs["app"].id == data["approved_app_id"]


async def test_request_without_owners_defaults_to_requester(db: Db) -> None:
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(requester=requester, requested_app_name="Solo").execute()
    assert ar.requested_owner_user_ids == [requester.id]


async def test_owner_tag_time_limit_caps_ownership(db: Db) -> None:
    tag = await TagFactory.create_async(constraints={Tag.OWNER_TIME_LIMIT_CONSTRAINT_KEY: 10 * 86400})
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(
        requester=requester, requested_app_name="Limited", requested_app_tags=[tag.id]
    ).execute()
    assert _close(ar.requested_ownership_ending_at, datetime.now(UTC) + 10 * DAY)


async def test_create_validation(db: Db, client: AsyncClient) -> None:
    await AppFactory.create_async(name="Taken")
    cases: list[tuple[dict[str, Any], int, str]] = [
        ({"requested_app_name": "taken"}, 409, "already exists"),
        ({"requested_app_name": "New", "requested_app_groups": [{"name": "Wrong-Name"}]}, 400, "prefixed"),
        ({"requested_app_name": "New", "requested_app_groups": [{"name": "App-New-Owners"}]}, 400, "created with"),
        (
            {"requested_app_name": "New", "requested_app_groups": [{"name": "App-New-A"}, {"name": "App-New-a"}]},
            400,
            "more than once",
        ),
        ({"requested_app_name": "New", "requested_owner_user_ids": ["nobody"]}, 400, "Unknown owner user ids"),
        (
            {"requested_app_name": "New", "requested_app_groups": [{"name": "App-New-A", "member_role_ids": ["x"]}]},
            400,
            "Unknown member role ids",
        ),
        ({"requested_app_name": "New", "requested_app_tags": ["missing"]}, 400, "tags not found"),
    ]
    for body, status, message in cases:
        rep = await client.post(AR_URL, json=body)
        assert rep.status_code == status, (body, rep.text)
        assert message in rep.json()["detail"], body


async def test_approval_refuses_name_taken_since_request(db: Db, client: AsyncClient, mock_user: Any) -> None:
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(requester=requester, requested_app_name="Racing").execute()
    ar_id = ar.id
    await AppFactory.create_async(name="Racing")

    rep = await client.put(f"{AR_URL}/{ar_id}", json={"approved": True})
    assert rep.status_code == 409
    refreshed = await db.session.get(AppRequest, ar_id)
    assert refreshed is not None
    await db.session.refresh(refreshed)
    assert refreshed.status == AccessRequestStatus.PENDING


async def test_requester_withdraws_and_retry_replaces(
    db: Db, client: AsyncClient, mock_user: Any, notifications: dict[str, Any]
) -> None:
    requester = await OktaUserFactory.create_async()
    mock_user(requester)
    first = (await client.post(AR_URL, json={"requested_app_name": "Retry"})).json()["id"]
    second = (await client.post(AR_URL, json={"requested_app_name": "retry"})).json()["id"]
    assert (await client.get(f"{AR_URL}/{first}")).json()["status"] == AccessRequestStatus.REJECTED

    rep = await client.put(f"{AR_URL}/{second}", json={"approved": False, "reason": "never mind"})
    assert rep.status_code == 200
    assert rep.json()["status"] == AccessRequestStatus.REJECTED
    # Withdrawing your own request doesn't notify you about it.
    assert notifications["completed"].call_args.kwargs["notify_requester"] is False


async def test_service_principal_must_name_an_owner(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, owner_id = await _principal(db)
    mock_user(sp_id)
    rep = await client.post(AR_URL, json={"requested_app_name": "Botless"})
    assert rep.status_code == 400
    assert "must name at least one owner" in rep.json()["detail"]

    rep = await client.post(AR_URL, json={"requested_app_name": "Botted", "requested_owner_user_ids": [owner_id]})
    assert rep.status_code == 201
    assert rep.json()["requester_service_principal"]["id"] == sp_id
    mine = (await client.get(AR_URL, params={"requester_user_id": "@me"})).json()["items"]
    assert [i["id"] for i in mine] == [rep.json()["id"]]

    # A service principal can't approve app requests.
    other = await CreateAppRequest(requester=await OktaUserFactory.create_async(), requested_app_name="Other").execute()
    assert (await client.put(f"{AR_URL}/{other.id}", json={"approved": True})).status_code == 403


@pytest.mark.parametrize("approved", [True, False])
async def test_conditional_access_decides_service_principal_request(
    db: Db, mocker: MockerFixture, notifications: dict[str, Any], approved: bool
) -> None:
    sp_id, owner_id = await _principal(db)
    evaluate = mocker.patch(
        "api.operations.create_app_request.evaluate_conditional_access",
        return_value=[
            ConditionalAccessResponse(approved=approved, reason="rule", ending_at=datetime.now(UTC) + 5 * DAY)
        ],
    )

    ar = await CreateAppRequest(
        requester=sp_id, requested_app_name="Tenant", requested_owner_user_ids=[owner_id]
    ).execute()
    assert isinstance(evaluate.call_args.kwargs["requester"], ServicePrincipal)
    await db.session.refresh(ar)
    if approved:
        assert ar.status == AccessRequestStatus.APPROVED
        assert ar.resolver_user_id is None
        app = await db.session.get(App, ar.approved_app_id)
        assert app is not None
        assert app.created_by_service_principal_id == sp_id
        owners = await _grants(db, await _group_id(db, "App-Tenant-Owners"))
        assert _close(owners["owner_users"][owner_id], datetime.now(UTC) + 5 * DAY)
    else:
        assert ar.status == AccessRequestStatus.REJECTED
    assert notifications["created"].call_count == 0
    assert notifications["completed"].call_count == 0


async def test_listing_filters(db: Db, client: AsyncClient, mock_user: Any) -> None:
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(requester=requester, requested_app_name="Findable").execute()

    assert [i["id"] for i in (await client.get(AR_URL, params={"q": "findab"})).json()["items"]] == [ar.id]
    assigned = (await client.get(AR_URL, params={"assignee_user_id": "@me", "status": "PENDING"})).json()["items"]
    assert [i["id"] for i in assigned] == [ar.id]

    mock_user(requester)
    assert (await client.get(AR_URL, params={"assignee_user_id": "@me"})).json()["items"] == []
    assert (await client.get(f"{AR_URL}/{ar.id}")).status_code == 200
    assert (await client.get(f"{AR_URL}/missing")).status_code == 404


async def test_deleting_requester_rejects_pending_app_request(db: Db) -> None:
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(requester=requester, requested_app_name="Orphan").execute()

    await DeleteUser(user=requester.id, sync_to_okta=False).execute()
    await db.session.refresh(ar)
    assert ar.status == AccessRequestStatus.REJECTED


async def test_approver_end_dates_replace_requested_and_tags_cap(db: Db, client: AsyncClient, mock_user: Any) -> None:
    tag = await TagFactory.create_async(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 20 * 86400})
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    member_id, requester_id = member.id, requester.id
    ar = await CreateAppRequest(
        requester=requester,
        requested_app_name="Dated",
        requested_app_tags=[tag.id],
        requested_app_groups=[AppRequestGroup(name="App-Dated-Users", member_user_ids=[member.id])],
        requested_ownership_ending_at=datetime.now(UTC) + 5 * DAY,
        requested_membership_ending_at=datetime.now(UTC) + 5 * DAY,
    ).execute()

    # Ownership is extended to 15 days; membership asks for 60 days and the tag caps it at 20.
    rep = await client.put(
        f"{AR_URL}/{ar.id}",
        json={
            "approved": True,
            "resolved_ownership_ending_at": (datetime.now(UTC) + 15 * DAY).isoformat(),
            "resolved_membership_ending_at": (datetime.now(UTC) + 60 * DAY).isoformat(),
        },
    )
    assert rep.status_code == 200, rep.text
    assert _close(datetime.fromisoformat(rep.json()["resolved_ownership_ending_at"]), datetime.now(UTC) + 15 * DAY)
    assert _close(datetime.fromisoformat(rep.json()["resolved_membership_ending_at"]), datetime.now(UTC) + 20 * DAY)
    owners = await _grants(db, await _group_id(db, "App-Dated-Owners"))
    assert _close(owners["owner_users"][requester_id], datetime.now(UTC) + 15 * DAY)
    users = await _grants(db, await _group_id(db, "App-Dated-Users"))
    assert _close(users["member_users"][member_id], datetime.now(UTC) + 20 * DAY)


async def test_app_group_names_follow_the_group_name_pattern(db: Db, client: AsyncClient) -> None:
    rep = await client.post(
        AR_URL, json={"requested_app_name": "Patterned", "requested_app_groups": [{"name": "App-Patterned-bad name!"}]}
    )
    assert rep.status_code == 400


async def test_no_membership_window_without_members(db: Db) -> None:
    tag = await TagFactory.create_async(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    ar = await CreateAppRequest(
        requester=await OktaUserFactory.create_async(), requested_app_name="Memberless", requested_app_tags=[tag.id]
    ).execute()
    assert ar.requested_membership_ending_at is None


async def test_groups_created_for_a_service_principal_record_it(
    db: Db, mocker: MockerFixture, notifications: dict[str, Any]
) -> None:
    sp_id, owner_id = await _principal(db)
    # A group already named under the app's prefix is absorbed, not created.
    absorbed = await OktaGroupFactory.create_async(name="App-Svc-Legacy")
    absorbed_id = absorbed.id
    mocker.patch(
        "api.operations.create_app_request.evaluate_conditional_access",
        return_value=[ConditionalAccessResponse(approved=True, reason="rule")],
    )
    ar = await CreateAppRequest(
        requester=sp_id,
        requested_app_name="Svc",
        requested_owner_user_ids=[owner_id],
        requested_app_groups=[AppRequestGroup(name="App-Svc-Deployers")],
    ).execute()
    ar_id = ar.id
    db.session.expire_all()
    ar = await db.session.get(AppRequest, ar_id)
    assert ar is not None
    assert ar.status == AccessRequestStatus.APPROVED
    # Approved automatically, even though absorbing a group reset the session, so no approver is asked.
    assert notifications["created"].call_count == 0

    created = {
        g.name: g.created_by_service_principal_id
        for g in (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == ar.approved_app_id))).all()
    }
    assert created["App-Svc-Owners"] == sp_id
    assert created["App-Svc-Deployers"] == sp_id
    absorbed_group = await db.session.get(OktaGroup, absorbed_id)
    assert absorbed_group is not None
    assert absorbed_group.created_by_service_principal_id is None


async def test_service_principal_withdrawal_and_deletion(db: Db, client: AsyncClient, mock_user: Any) -> None:
    sp_id, owner_id = await _principal(db)
    admin_id = await _admin_id(db)
    mock_user(sp_id)
    withdrawn = (
        await client.post(AR_URL, json={"requested_app_name": "Withdrawn", "requested_owner_user_ids": [owner_id]})
    ).json()["id"]
    stranded = (
        await client.post(AR_URL, json={"requested_app_name": "Stranded", "requested_owner_user_ids": [owner_id]})
    ).json()["id"]

    rep = await client.put(f"{AR_URL}/{withdrawn}", json={"approved": False})
    assert rep.json()["resolver"] is None
    assert rep.json()["resolver_service_principal"]["id"] == sp_id

    mock_user(admin_id)
    assert (await client.delete(f"/api/service-principals/{sp_id}")).status_code == 200
    assert (await client.get(f"{AR_URL}/{stranded}")).json()["status"] == AccessRequestStatus.REJECTED


async def test_invalid_retry_keeps_the_pending_request(db: Db, client: AsyncClient, mock_user: Any) -> None:
    requester = await OktaUserFactory.create_async()
    mock_user(requester.id)
    first = (await client.post(AR_URL, json={"requested_app_name": "Keep"})).json()["id"]
    rep = await client.post(AR_URL, json={"requested_app_name": "Keep", "requested_owner_user_ids": ["nobody"]})
    assert rep.status_code == 400
    assert (await client.get(f"{AR_URL}/{first}")).json()["status"] == AccessRequestStatus.PENDING


async def test_departed_principals_are_skipped_with_a_note(db: Db, client: AsyncClient) -> None:
    requester = await OktaUserFactory.create_async()
    gone = await OktaUserFactory.create_async()
    stays = await OktaUserFactory.create_async()
    gone_id, stays_id = gone.id, stays.id
    ar = await CreateAppRequest(
        requester=requester,
        requested_app_name="Departed",
        requested_app_groups=[AppRequestGroup(name="App-Departed-Users", member_user_ids=[gone.id, stays.id])],
    ).execute()
    ar_id = ar.id
    gone.deleted_at = gone.created_at
    await db.session.commit()

    rep = await client.put(f"{AR_URL}/{ar_id}", json={"approved": True, "reason": "ok"})
    assert rep.status_code == 200, rep.text
    assert rep.json()["status"] == AccessRequestStatus.APPROVED
    assert f"user {gone_id}" in rep.json()["resolution_reason"]
    users = await _grants(db, await _group_id(db, "App-Departed-Users"))
    assert set(users["member_users"]) == {stays_id}


async def test_failed_approval_doesnt_keep_the_approvers_dates(db: Db, client: AsyncClient) -> None:
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(requester=requester, requested_app_name="Retried").execute()
    ar_id = ar.id
    await AppFactory.create_async(name="Retried")

    later = (datetime.now(UTC) + 40 * DAY).isoformat()
    rep = await client.put(f"{AR_URL}/{ar_id}", json={"approved": True, "resolved_ownership_ending_at": later})
    assert rep.status_code == 409
    stored = await db.session.get(AppRequest, ar_id)
    assert stored is not None
    await db.session.refresh(stored)
    assert stored.resolved_ownership_ending_at is None


async def test_failed_grant_still_approves_the_app(db: Db, client: AsyncClient, mocker: MockerFixture) -> None:
    requester = await OktaUserFactory.create_async()
    good_role = await RoleGroupFactory.create_async()
    bad_role = await RoleGroupFactory.create_async()
    ids = {"good": good_role.id, "bad": bad_role.id}
    ar = await CreateAppRequest(
        requester=requester,
        requested_app_name="Flaky",
        requested_app_groups=[AppRequestGroup(name="App-Flaky-Users", member_role_ids=[good_role.id, bad_role.id])],
    ).execute()
    ar_id = ar.id

    from api.operations import modify_role_groups

    real_execute = modify_role_groups.ModifyRoleGroups.execute

    async def flaky(self: Any) -> Any:
        if self.role_group_id == ids["bad"]:
            raise RuntimeError("role vanished")
        return await real_execute(self)

    mocker.patch.object(modify_role_groups.ModifyRoleGroups, "execute", flaky)
    rep = await client.put(f"{AR_URL}/{ar_id}", json={"approved": True})
    assert rep.status_code == 200, rep.text
    assert rep.json()["status"] == AccessRequestStatus.APPROVED
    assert f"App-Flaky-Users member role {ids['bad']}" in rep.json()["resolution_reason"]
    users = await _grants(db, await _group_id(db, "App-Flaky-Users"))
    assert set(users["member_roles"]) == {ids["good"]}


async def test_another_apps_group_cant_be_named(db: Db, client: AsyncClient) -> None:
    # "Foo-Bar" is its own app, and its group's name also carries the prefix of an app named "Foo".
    other_app = await AppFactory.create_async(name="Foo-Bar")
    other_group = await AppGroupFactory.create_async(app_id=other_app.id, is_owner=False, name="App-Foo-Bar-Admins")
    other_app_id, other_group_id = other_app.id, other_group.id

    rep = await client.post(
        AR_URL, json={"requested_app_name": "Foo", "requested_app_groups": [{"name": "App-Foo-Bar-Admins"}]}
    )
    assert rep.status_code == 409

    # A group claimed by another app after the request was filed is refused at approval.
    ar = await CreateAppRequest(
        requester=await OktaUserFactory.create_async(),
        requested_app_name="Foo",
        requested_app_groups=[AppRequestGroup(name="App-Foo-Late")],
    ).execute()
    ar_id = ar.id
    await AppGroupFactory.create_async(app_id=other_app_id, is_owner=False, name="App-Foo-Late")
    rep = await client.put(f"{AR_URL}/{ar_id}", json={"approved": True})
    assert rep.status_code == 409
    group = await db.session.get(AppGroup, other_group_id)
    assert group is not None
    assert group.app_id == other_app_id


async def test_approving_for_a_departed_requester_fails(db: Db, client: AsyncClient) -> None:
    requester = await OktaUserFactory.create_async()
    ar = await CreateAppRequest(
        requester=requester, requested_owner_user_ids=[(await _admin_id(db))], requested_app_name="Gone"
    ).execute()
    ar_id = ar.id
    requester.deleted_at = requester.created_at
    await db.session.commit()

    rep = await client.put(f"{AR_URL}/{ar_id}", json={"approved": True})
    assert rep.status_code == 409
    assert (await client.get(f"{AR_URL}/{ar_id}")).json()["status"] == AccessRequestStatus.PENDING
