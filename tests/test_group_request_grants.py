"""Named owners and members on group requests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from faker import Faker
from httpx import AsyncClient
from okta.models import Group
from pytest_mock import MockerFixture
from sqlalchemy import func, or_, select

from api.config import settings
from api.exceptions import InvalidRequestError
from api.extensions import Db
from api.models import (
    AccessRequestStatus,
    App,
    AppGroup,
    GroupRequest,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    Tag,
)
from api.operations import ApproveGroupRequest, CreateGroupRequest
from api.plugins import ConditionalAccessResponse
from api.services import okta
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    AppTagMapFactory,
    OktaUserFactory,
    OktaUserGroupMemberFactory,
    RoleGroupFactory,
    TagFactory,
)

GR_URL = "/api/group-requests"
DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def _okta(mocker: MockerFixture, faker: Faker) -> None:  # type: ignore[type-arg]
    mocker.patch.object(
        okta, "create_group", side_effect=lambda name, desc: Group.from_dict({"id": faker.pystr(20, 20)})
    )
    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "add_user_to_group")


async def _admin(db: Db) -> OktaUser:
    return (await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))).one()


async def _grants(db: Db, group_id: str) -> dict[str, Any]:
    """The active direct user grants and role grants on a group."""
    active_user = or_(OktaUserGroupMember.ended_at.is_(None), OktaUserGroupMember.ended_at > func.now())
    active_role = or_(RoleGroupMap.ended_at.is_(None), RoleGroupMap.ended_at > func.now())
    users = (
        await db.session.scalars(
            select(OktaUserGroupMember)
            .where(OktaUserGroupMember.group_id == group_id)
            .where(OktaUserGroupMember.role_group_map_id.is_(None))
            .where(active_user)
        )
    ).all()
    roles = (
        await db.session.scalars(select(RoleGroupMap).where(RoleGroupMap.group_id == group_id).where(active_role))
    ).all()
    return {
        "owner_users": {m.user_id: m.ended_at for m in users if m.is_owner},
        "member_users": {m.user_id: m.ended_at for m in users if not m.is_owner},
        "owner_roles": {m.role_group_id: m.ended_at for m in roles if m.is_owner},
        "member_roles": {m.role_group_id: m.ended_at for m in roles if not m.is_owner},
    }


def _close(actual: datetime | None, expected: datetime) -> bool:
    assert actual is not None
    return abs(actual.replace(tzinfo=UTC) - expected) < timedelta(minutes=5)


async def test_request_without_owners_defaults_to_requester(db: Db) -> None:
    requester = await OktaUserFactory.create_async()
    gr = await CreateGroupRequest(
        requester_user=requester, requested_group_name="Plain", requested_group_type="okta_group"
    ).execute()
    assert gr is not None
    assert gr.requested_owner_user_ids == [requester.id]
    assert gr.requested_owner_role_ids == []


async def test_approval_grants_named_owners_and_members(db: Db, client: AsyncClient) -> None:
    requester = await OktaUserFactory.create_async()
    owner = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    owner_role = await RoleGroupFactory.create_async()
    member_role = await RoleGroupFactory.create_async()
    ids = {
        "requester": requester.id,
        "owner": owner.id,
        "member": member.id,
        "owner_role": owner_role.id,
        "member_role": member_role.id,
    }
    owner_end = datetime.now(UTC) + 30 * DAY
    member_end = datetime.now(UTC) + 10 * DAY

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Named",
        requested_group_type="okta_group",
        request_reason="bootstrap",
        requested_owner_user_ids=[owner.id],
        requested_owner_role_ids=[owner_role.id],
        requested_member_user_ids=[member.id],
        requested_member_role_ids=[member_role.id],
        requested_ownership_ending_at=owner_end,
        requested_membership_ending_at=member_end,
    ).execute()
    assert gr is not None
    gr_id = gr.id

    rep = await client.put(f"{GR_URL}/{gr_id}", json={"approved": True, "reason": "ok"})
    assert rep.status_code == 200, rep.text
    data = rep.json()
    assert data["status"] == AccessRequestStatus.APPROVED
    assert data["resolved_owner_user_ids"] == [ids["owner"]]
    assert data["resolved_owner_role_ids"] == [ids["owner_role"]]
    assert data["resolved_member_user_ids"] == [ids["member"]]
    assert data["resolved_member_role_ids"] == [ids["member_role"]]
    assert {u["id"] for u in data["named_users"]} == {ids["owner"], ids["member"]}
    assert {r["id"] for r in data["named_roles"]} == {ids["owner_role"], ids["member_role"]}

    grants = await _grants(db, data["approved_group_id"])
    # The requester named other owners, so it is not made an owner.
    assert set(grants["owner_users"]) == {ids["owner"]}
    assert _close(grants["owner_users"][ids["owner"]], owner_end)
    assert _close(grants["member_users"][ids["member"]], member_end)
    assert _close(grants["owner_roles"][ids["owner_role"]], owner_end)
    assert _close(grants["member_roles"][ids["member_role"]], member_end)
    assert set(grants["member_roles"]) == {ids["member_role"]}


async def test_approver_can_replace_named_grants(db: Db, client: AsyncClient) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    replacement = await OktaUserFactory.create_async()
    replacement_id = replacement.id
    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Replace",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
    ).execute()
    assert gr is not None

    rep = await client.put(
        f"{GR_URL}/{gr.id}",
        json={"approved": True, "resolved_owner_user_ids": [replacement_id], "resolved_member_user_ids": []},
    )
    assert rep.status_code == 200, rep.text
    grants = await _grants(db, rep.json()["approved_group_id"])
    assert set(grants["owner_users"]) == {replacement_id}
    assert grants["member_users"] == {}


async def test_approver_cannot_remove_every_owner(db: Db, client: AsyncClient) -> None:
    gr = await CreateGroupRequest(
        requester_user=await OktaUserFactory.create_async(),
        requested_group_name="Ownerless",
        requested_group_type="okta_group",
    ).execute()
    assert gr is not None
    gr_id = gr.id

    rep = await client.put(
        f"{GR_URL}/{gr_id}",
        json={"approved": True, "resolved_owner_user_ids": [], "resolved_owner_role_ids": []},
    )
    assert rep.status_code == 400
    assert (await client.get(f"{GR_URL}/{gr_id}")).json()["status"] == AccessRequestStatus.PENDING


async def test_create_validates_named_grants(db: Db, client: AsyncClient) -> None:
    role = await RoleGroupFactory.create_async()
    role_id = role.id

    rep = await client.post(
        GR_URL,
        json={"requested_group_name": "Bad", "requested_group_type": "okta_group", "requested_owner_user_ids": ["x"]},
    )
    assert rep.status_code == 400
    assert "Unknown owner user ids: x" in rep.json()["detail"]

    rep = await client.post(
        GR_URL,
        json={"requested_group_name": "Bad", "requested_group_type": "okta_group", "requested_member_role_ids": ["y"]},
    )
    assert rep.status_code == 400
    assert "Unknown member role ids: y" in rep.json()["detail"]

    rep = await client.post(
        GR_URL,
        json={
            "requested_group_name": "Role-Nested",
            "requested_group_type": "role_group",
            "requested_member_role_ids": [role_id],
        },
    )
    assert rep.status_code == 400
    assert "Roles cannot be added to other Roles" in rep.json()["detail"]


async def test_member_tag_time_limit_caps_member_grants(db: Db) -> None:
    app = await AppFactory.create_async()
    tag = await TagFactory.create_async(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 7 * 86400})
    await AppTagMapFactory.create_async(app_id=app.id, tag_id=tag.id)
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    member_id = member.id
    admin = await _admin(db)

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app.name}-Capped",
        requested_group_type="app_group",
        requested_app_id=app.id,
        requested_member_user_ids=[member.id],
        requested_membership_ending_at=datetime.now(UTC) + 30 * DAY,
    ).execute()
    assert gr is not None
    # The request carries no tags of its own, so the app's limit applies at approval.
    await ApproveGroupRequest(group_request=gr, approver_user=admin).execute()
    await db.session.refresh(gr)

    expected = datetime.now(UTC) + 7 * DAY
    assert _close(gr.resolved_membership_ending_at, expected)
    # Ownership carries no limit, so the requester's ownership stays indefinite.
    assert gr.resolved_ownership_ending_at is None
    assert gr.approved_group_id is not None
    grants = await _grants(db, gr.approved_group_id)
    assert _close(grants["member_users"][member_id], expected)


@pytest.mark.parametrize(
    ("requested_days", "plugin_days", "expected_days"),
    [(None, 7, 7), (3, 7, 3), (7, 3, 3)],
)
async def test_plugin_membership_ending_at_caps_members(
    db: Db, mocker: MockerFixture, requested_days: int | None, plugin_days: int, expected_days: int
) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    member_id = member.id
    mocker.patch(
        "api.operations.create_group_request.evaluate_conditional_access",
        return_value=[
            ConditionalAccessResponse(
                approved=True,
                membership_ending_at=datetime.now(UTC) + plugin_days * DAY,
                allow_named_grants=True,
            )
        ],
    )

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Plugin",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
        requested_membership_ending_at=(
            datetime.now(UTC) + requested_days * DAY if requested_days is not None else None
        ),
    ).execute()
    assert gr is not None
    await db.session.refresh(gr)
    assert gr.status == AccessRequestStatus.APPROVED
    assert gr.approved_group_id is not None
    grants = await _grants(db, gr.approved_group_id)
    assert _close(grants["member_users"][member_id], datetime.now(UTC) + expected_days * DAY)
    # `membership_ending_at` leaves the owner grants alone.
    assert grants["owner_users"] == {gr.requester_user_id: None}


async def test_past_membership_window_blocks_approval(db: Db) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Lapsed",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
    ).execute()
    assert gr is not None
    gr.requested_membership_ending_at = datetime.now(UTC) - DAY
    await db.session.commit()

    with pytest.raises(InvalidRequestError, match="membership window"):
        await ApproveGroupRequest(group_request=gr, approver_user=await _admin(db)).execute()


async def _app_owner(db: Db, app: App) -> OktaUser:
    owner_group = await AppGroupFactory.create_async(
        app_id=app.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        f"{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
    )
    owner = await OktaUserFactory.create_async()
    await OktaUserGroupMemberFactory.create_async(user_id=owner.id, group_id=owner_group.id, is_owner=True)
    return owner


async def _self_add_tagged_app(db: Db, key: str) -> App:
    app = await AppFactory.create_async()
    tag = await TagFactory.create_async(constraints={key: True})
    await AppTagMapFactory.create_async(app_id=app.id, tag_id=tag.id)
    return app


async def test_approver_cannot_approve_own_ownership_against_self_add_constraint(db: Db) -> None:
    app = await _self_add_tagged_app(db, Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY)
    approver = await _app_owner(db, app)
    requester = await OktaUserFactory.create_async()

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app.name}-SelfOwner",
        requested_group_type="app_group",
        requested_app_id=app.id,
        requested_owner_user_ids=[approver.id],
    ).execute()
    assert gr is not None

    with pytest.raises(InvalidRequestError, match="restricted from adding themself as an owner"):
        await ApproveGroupRequest(group_request=gr, approver_user=approver).execute()
    await db.session.refresh(gr)
    assert gr.status == AccessRequestStatus.PENDING
    assert gr.approved_group_id is None


async def test_approver_cannot_add_own_role_as_member_against_self_add_constraint(db: Db) -> None:
    app = await _self_add_tagged_app(db, Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    approver = await _app_owner(db, app)
    role: RoleGroup = await RoleGroupFactory.create_async()
    await OktaUserGroupMemberFactory.create_async(user_id=approver.id, group_id=role.id, is_owner=False)
    requester = await OktaUserFactory.create_async()

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app.name}-SelfRole",
        requested_group_type="app_group",
        requested_app_id=app.id,
        requested_member_role_ids=[role.id],
    ).execute()
    assert gr is not None

    with pytest.raises(InvalidRequestError, match="restricted from adding themself as a member"):
        await ApproveGroupRequest(group_request=gr, approver_user=approver).execute()


async def test_app_owner_auto_approval_skips_blocked_self_grant(db: Db) -> None:
    app = await _self_add_tagged_app(db, Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY)
    app_owner = await _app_owner(db, app)
    other_owner = await OktaUserFactory.create_async()
    app_owner_id, other_owner_id = app_owner.id, other_owner.id

    gr = await CreateGroupRequest(
        requester_user=app_owner,
        requested_group_name=f"App-{app.name}-Auto",
        requested_group_type="app_group",
        requested_app_id=app.id,
        requested_owner_user_ids=[app_owner.id, other_owner.id],
    ).execute()
    assert gr is not None
    await db.session.refresh(gr)
    assert gr.status == AccessRequestStatus.APPROVED
    assert gr.resolved_owner_user_ids == [other_owner_id]
    assert gr.approved_group_id is not None
    grants = await _grants(db, gr.approved_group_id)
    assert app_owner_id not in grants["owner_users"]
    assert other_owner_id in grants["owner_users"]


async def test_admin_is_exempt_from_self_add_constraint(db: Db) -> None:
    app = await _self_add_tagged_app(db, Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY)
    admin = await _admin(db)
    requester = await OktaUserFactory.create_async()

    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name=f"App-{app.name}-Admin",
        requested_group_type="app_group",
        requested_app_id=app.id,
        requested_owner_user_ids=[admin.id],
    ).execute()
    assert gr is not None
    await ApproveGroupRequest(group_request=gr, approver_user=admin).execute()
    refreshed = await db.session.get(GroupRequest, gr.id)
    assert refreshed is not None
    await db.session.refresh(refreshed)
    assert refreshed.status == AccessRequestStatus.APPROVED


async def test_plugin_approval_of_named_grants_must_opt_in(db: Db, mocker: MockerFixture) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    mocker.patch(
        "api.operations.create_group_request.evaluate_conditional_access",
        return_value=[ConditionalAccessResponse(approved=True, reason="legacy rule")],
    )

    # A rule written for "the requester owns it" still approves that request...
    plain = await CreateGroupRequest(
        requester_user=requester, requested_group_name="Plain-Approved", requested_group_type="okta_group"
    ).execute()
    assert plain is not None
    await db.session.refresh(plain)
    assert plain.status == AccessRequestStatus.APPROVED

    # ...but not one naming more, which waits for a reviewer.
    named = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Named-Pending",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
    ).execute()
    assert named is not None
    await db.session.refresh(named)
    assert named.status == AccessRequestStatus.PENDING


async def test_no_membership_window_without_members(db: Db) -> None:
    tag = await TagFactory.create_async(constraints={Tag.MEMBER_TIME_LIMIT_CONSTRAINT_KEY: 86400})
    requester = await OktaUserFactory.create_async()
    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Ownerly",
        requested_group_type="okta_group",
        requested_group_tags=[tag.id],
    ).execute()
    assert gr is not None
    assert gr.requested_membership_ending_at is None

    # Even a stale membership date doesn't block a request that grants no membership.
    gr.requested_membership_ending_at = datetime.now(UTC) - DAY
    await db.session.commit()
    await ApproveGroupRequest(group_request=gr, approver_user=await _admin(db)).execute()
    await db.session.refresh(gr)
    assert gr.status == AccessRequestStatus.APPROVED
    assert gr.resolved_membership_ending_at is None


async def test_approver_sets_membership_end_date(db: Db, client: AsyncClient) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    member_id = member.id
    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Approver-Dates",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
        requested_membership_ending_at=datetime.now(UTC) + 10 * DAY,
    ).execute()
    assert gr is not None
    chosen = datetime.now(UTC) + 3 * DAY

    # Like the ownership end date, a sent membership end date replaces the requested one.
    rep = await client.put(
        f"{GR_URL}/{gr.id}", json={"approved": True, "resolved_membership_ending_at": chosen.isoformat()}
    )
    assert rep.status_code == 200, rep.text
    grants = await _grants(db, rep.json()["approved_group_id"])
    assert _close(grants["member_users"][member_id], chosen)


async def test_app_owner_auto_approval_failure_leaves_request_pending(db: Db) -> None:
    app = await AppFactory.create_async()
    app_owner = await _app_owner(db, app)
    member = await OktaUserFactory.create_async()

    # An already-passed membership window makes the auto-approval fail; the request
    # is saved, so it goes to reviewers instead of the caller getting an error.
    gr = await CreateGroupRequest(
        requester_user=app_owner,
        requested_group_name=f"App-{app.name}-Lapsed",
        requested_group_type="app_group",
        requested_app_id=app.id,
        requested_member_user_ids=[member.id],
        requested_membership_ending_at=datetime.now(UTC) - DAY,
    ).execute()
    assert gr is not None
    await db.session.refresh(gr)
    assert gr.status == AccessRequestStatus.PENDING


async def test_ignored_plugin_approval_stops_evaluation(db: Db, mocker: MockerFixture) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    mocker.patch(
        "api.operations.create_group_request.evaluate_conditional_access",
        return_value=[
            ConditionalAccessResponse(approved=True, reason="legacy rule"),
            ConditionalAccessResponse(approved=False, reason="a later plugin"),
        ],
    )
    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="First-Decision",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
    ).execute()
    assert gr is not None
    await db.session.refresh(gr)
    assert gr.status == AccessRequestStatus.PENDING


async def test_failed_grant_still_approves_with_the_rest(db: Db, mocker: MockerFixture) -> None:
    requester = await OktaUserFactory.create_async()
    member = await OktaUserFactory.create_async()
    good_role = await RoleGroupFactory.create_async()
    bad_role = await RoleGroupFactory.create_async()
    ids = {"member": member.id, "good": good_role.id, "bad": bad_role.id}
    gr = await CreateGroupRequest(
        requester_user=requester,
        requested_group_name="Partial",
        requested_group_type="okta_group",
        requested_member_user_ids=[member.id],
        requested_member_role_ids=[good_role.id, bad_role.id],
    ).execute()
    assert gr is not None
    gr_id = gr.id

    from api.operations import modify_role_groups

    real_execute = modify_role_groups.ModifyRoleGroups.execute

    async def flaky(self: Any) -> Any:
        if self.role_group_id == ids["bad"]:
            raise RuntimeError("role vanished")
        return await real_execute(self)

    mocker.patch.object(modify_role_groups.ModifyRoleGroups, "execute", flaky)
    await ApproveGroupRequest(group_request=gr_id, approver_user=await _admin(db)).execute()

    reloaded = await db.session.get(GroupRequest, gr_id)
    assert reloaded is not None
    await db.session.refresh(reloaded)
    assert reloaded.status == AccessRequestStatus.APPROVED
    assert reloaded.resolved_member_role_ids == [ids["good"]]
    assert f"member role {ids['bad']}" in reloaded.resolution_reason
    assert reloaded.approved_group_id is not None
    grants = await _grants(db, reloaded.approved_group_id)
    assert set(grants["member_roles"]) == {ids["good"]}
    assert set(grants["member_users"]) == {ids["member"]}
