"""Who reviews each kind of request."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.auth.dependencies import get_current_user_id
from api.config import settings
from api.extensions import Db
from api.models import (
    AccessRequest,
    App,
    AppGroup,
    GroupRequest,
    OktaGroup,
    OktaUser,
    RoleRequest,
    Tag,
)
from api.models.request_reviewers import (
    OwnerLevel,
    get_assigned_reviewers,
    get_eligible_reviewers_by_level,
)
from api.operations.constraints import CheckForSelfAdd
from tests.factories import (
    AccessRequestFactory,
    AppFactory,
    AppGroupFactory,
    AppTagMapFactory,
    GroupRequestFactory,
    OktaGroupFactory,
    OktaGroupTagMapFactory,
    OktaUserFactory,
    OktaUserGroupMemberFactory,
    RoleGroupFactory,
    RoleGroupMapFactory,
    RoleRequestFactory,
    TagFactory,
)
from tests.request_factories import (
    ResolveAccessRequestBodyFactory,
    ResolveGroupRequestBodyFactory,
    ResolveRoleRequestBodyFactory,
)


@dataclass
class Scenario:
    """A persisted request plus the names of the users it involves."""

    request: AccessRequest | RoleRequest | GroupRequest
    users: dict[str, OktaUser]
    expected_level: Optional[OwnerLevel]
    expected_reviewers: set[str]
    expected_by_level: list[tuple[OwnerLevel, set[str]]] = field(default_factory=list)


async def _admin() -> OktaUser:
    """The Access admin seeded by the `db` fixture."""
    from api.extensions import db

    admin = (await db.session.scalars(select(OktaUser).where(OktaUser.email == settings.CURRENT_OKTA_USER_EMAIL))).one()
    return admin


async def _make_admin(user: OktaUser) -> None:
    """Make `user` an Access admin (a member of the Access app's owners group)."""
    from api.extensions import db

    owners_group = (
        await db.session.scalars(
            select(AppGroup)
            .join(App, App.id == AppGroup.app_id)
            .where(App.name == App.ACCESS_APP_RESERVED_NAME, AppGroup.is_owner.is_(True))
        )
    ).one()
    await OktaUserGroupMemberFactory.create_async(user_id=user.id, group_id=owners_group.id, is_owner=False)


async def _own(user: OktaUser, group: OktaGroup, **kwargs: object) -> None:
    """Make `user` an owner of `group`."""
    await OktaUserGroupMemberFactory.create_async(user_id=user.id, group_id=group.id, is_owner=True, **kwargs)


async def _join(user: OktaUser, group: OktaGroup) -> None:
    """Make `user` a member of `group`."""
    await OktaUserGroupMemberFactory.create_async(user_id=user.id, group_id=group.id, is_owner=False)


async def _app(*owners: OktaUser) -> tuple[App, AppGroup]:
    """An app, its owners group, and `owners` as owners of that group."""
    app = await AppFactory.create_async()
    owners_group = await AppGroupFactory.create_async(app_id=app.id, is_owner=True)
    for owner in owners:
        await _own(owner, owners_group)
    return app, owners_group


async def _users(*names: str) -> dict[str, OktaUser]:
    """A fresh user per name, plus the seeded Access admin as "admin"."""
    users = {name: await OktaUserFactory.create_async() for name in names}
    users["admin"] = await _admin()
    return users


async def _tag(key: str, *, enabled: bool = True) -> Tag:
    """A tag that turns on the boolean constraint `key`."""
    return await TagFactory.create_async(constraints={key: True}, enabled=enabled)


# --- Access requests (individual) ---------------------------------------------------------


async def access_group_owners() -> Scenario:
    u = await _users("requester", "owner1", "owner2")
    group = await OktaGroupFactory.create_async()
    await _own(u["owner1"], group)
    await _own(u["owner2"], group)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "group_owners",
        {"owner1", "owner2"},
        [("group_owners", {"owner1", "owner2"}), ("access_admins", {"admin"})],
    )


async def access_sole_owner_is_requester_plain_group() -> Scenario:
    u = await _users("requester")
    group = await OktaGroupFactory.create_async()
    await _own(u["requester"], group)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def access_app_group_without_group_owners() -> Scenario:
    u = await _users("requester", "app_owner")
    app, _ = await _app(u["app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "app_owners",
        {"app_owner"},
        [("app_owners", {"app_owner"}), ("access_admins", {"admin"})],
    )


async def access_requester_is_sole_group_owner_and_an_app_owner() -> Scenario:
    u = await _users("requester", "app_owner")
    app, _ = await _app(u["app_owner"], u["requester"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    await _own(u["requester"], group)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "app_owners",
        {"app_owner"},
        [("app_owners", {"app_owner"}), ("access_admins", {"admin"})],
    )


async def access_unowned_app() -> Scenario:
    u = await _users("requester")
    app, _ = await _app()
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def access_app_owners_group_skips_duplicate_level() -> Scenario:
    """A request for an app's owners group lists its owners once, as group owners."""
    u = await _users("requester", "app_owner")
    _, owners_group = await _app(u["app_owner"])
    req = await AccessRequestFactory.create_async(
        requester_user_id=u["requester"].id, requested_group_id=owners_group.id
    )
    return Scenario(
        req,
        u,
        "group_owners",
        {"app_owner"},
        [("group_owners", {"app_owner"}), ("access_admins", {"admin"})],
    )


async def access_admin_requester_with_another_admin() -> Scenario:
    u = await _users("admin2")
    await _make_admin(u["admin2"])
    group = await OktaGroupFactory.create_async()
    req = await AccessRequestFactory.create_async(requester_user_id=u["admin"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin2"},
        [("access_admins", {"admin2"})],
    )


async def access_nobody_eligible() -> Scenario:
    u = await _users()
    group = await OktaGroupFactory.create_async()
    req = await AccessRequestFactory.create_async(requester_user_id=u["admin"].id, requested_group_id=group.id)
    return Scenario(req, u, None, set(), [])


async def access_ended_ownership_ignored() -> Scenario:
    u = await _users("requester", "former_owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["former_owner"], group, ended_at=datetime.now(timezone.utc) - timedelta(days=1))
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def access_deleted_owner_ignored() -> Scenario:
    from api.extensions import db

    u = await _users("requester", "deleted_owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["deleted_owner"], group)
    u["deleted_owner"].deleted_at = datetime.now(timezone.utc)
    await db.session.commit()
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


# --- Role requests --------------------------------------------------------------------------


async def _role_request(
    u: dict[str, OktaUser], group: OktaGroup, *, role_members: tuple[str, ...], ownership: bool = False
) -> RoleRequest:
    """A role request from a new role that `requester` owns and `role_members` belong to."""
    role = await RoleGroupFactory.create_async()
    await _own(u["requester"], role)
    for name in role_members:
        await _join(u[name], role)
    return await RoleRequestFactory.create_async(
        requester_user_id=u["requester"].id,
        requester_role_id=role.id,
        requested_group_id=group.id,
        request_ownership=ownership,
    )


async def role_tag_blocks_one_group_owner() -> Scenario:
    u = await _users("requester", "blocked_owner", "owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["blocked_owner"], group)
    await _own(u["owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("blocked_owner",))
    return Scenario(
        req,
        u,
        "group_owners",
        {"owner"},
        [("group_owners", {"owner"}), ("access_admins", {"admin"})],
    )


async def role_tag_blocks_every_group_owner() -> Scenario:
    u = await _users("requester", "blocked_owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["blocked_owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("blocked_owner",))
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def role_ownership_tag_blocks_ownership_request() -> Scenario:
    u = await _users("requester", "blocked_owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["blocked_owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("blocked_owner",), ownership=True)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def role_membership_tag_does_not_block_ownership_request() -> Scenario:
    u = await _users("requester", "owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("owner",), ownership=True)
    return Scenario(
        req,
        u,
        "group_owners",
        {"owner"},
        [("group_owners", {"owner"}), ("access_admins", {"admin"})],
    )


async def role_disabled_tag_does_not_block() -> Scenario:
    u = await _users("requester", "owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY, enabled=False)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("owner",))
    return Scenario(
        req,
        u,
        "group_owners",
        {"owner"},
        [("group_owners", {"owner"}), ("access_admins", {"admin"})],
    )


async def role_app_inherited_tag_blocks() -> Scenario:
    u = await _users("requester", "blocked_owner")
    app, _ = await _app()
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    await _own(u["blocked_owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    app_tag_map = await AppTagMapFactory.create_async(app_id=app.id, tag_id=tag.id)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id, app_tag_map_id=app_tag_map.id)
    req = await _role_request(u, group, role_members=("blocked_owner",))
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def role_tag_blocks_app_owner() -> Scenario:
    u = await _users("requester", "blocked_app_owner")
    app, _ = await _app(u["blocked_app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("blocked_app_owner",))
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def role_ownership_tag_blocks_app_owner() -> Scenario:
    u = await _users("requester", "blocked_app_owner")
    app, _ = await _app(u["blocked_app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("blocked_app_owner",), ownership=True)
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def role_admin_exempt_from_tag() -> Scenario:
    u = await _users("requester")
    group = await OktaGroupFactory.create_async()
    await _own(u["admin"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("admin",))
    return Scenario(
        req,
        u,
        "group_owners",
        {"admin"},
        [("group_owners", {"admin"})],
    )


# --- Group requests -------------------------------------------------------------------------


async def group_app_group_with_app_owners() -> Scenario:
    u = await _users("requester", "app_owner")
    app, _ = await _app(u["app_owner"])
    req = await GroupRequestFactory.create_async(
        requester_user_id=u["requester"].id, requested_group_type="app_group", requested_app_id=app.id
    )
    return Scenario(
        req,
        u,
        "app_owners",
        {"app_owner"},
        [("app_owners", {"app_owner"}), ("access_admins", {"admin"})],
    )


async def group_app_group_unowned_app() -> Scenario:
    u = await _users("requester")
    app, _ = await _app()
    req = await GroupRequestFactory.create_async(
        requester_user_id=u["requester"].id, requested_group_type="app_group", requested_app_id=app.id
    )
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def group_requester_is_sole_app_owner() -> Scenario:
    u = await _users("requester")
    app, _ = await _app(u["requester"])
    req = await GroupRequestFactory.create_async(
        requester_user_id=u["requester"].id, requested_group_type="app_group", requested_app_id=app.id
    )
    return Scenario(
        req,
        u,
        "access_admins",
        {"admin"},
        [("access_admins", {"admin"})],
    )


async def group_plain_group() -> Scenario:
    u = await _users("requester")
    req = await GroupRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_type="okta_group")
    return Scenario(req, u, "access_admins", {"admin"}, [("access_admins", {"admin"})])


async def group_role_group() -> Scenario:
    u = await _users("requester")
    req = await GroupRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_type="role_group")
    return Scenario(req, u, "access_admins", {"admin"}, [("access_admins", {"admin"})])


SCENARIOS: list[Callable[[], Awaitable[Scenario]]] = [
    access_group_owners,
    access_sole_owner_is_requester_plain_group,
    access_app_group_without_group_owners,
    access_requester_is_sole_group_owner_and_an_app_owner,
    access_unowned_app,
    access_app_owners_group_skips_duplicate_level,
    access_admin_requester_with_another_admin,
    access_nobody_eligible,
    access_ended_ownership_ignored,
    access_deleted_owner_ignored,
    role_tag_blocks_one_group_owner,
    role_tag_blocks_every_group_owner,
    role_ownership_tag_blocks_ownership_request,
    role_membership_tag_does_not_block_ownership_request,
    role_disabled_tag_does_not_block,
    role_app_inherited_tag_blocks,
    role_tag_blocks_app_owner,
    role_ownership_tag_blocks_app_owner,
    role_admin_exempt_from_tag,
    group_app_group_with_app_owners,
    group_app_group_unowned_app,
    group_requester_is_sole_app_owner,
    group_plain_group,
    group_role_group,
]


def _names(scenario: Scenario, users: list[OktaUser]) -> set[str]:
    """The scenario names of `users`; raises KeyError for anyone outside the scenario."""
    by_id = {user.id: name for name, user in scenario.users.items()}
    return {by_id[user.id] for user in users}


@pytest.mark.parametrize("build", SCENARIOS, ids=lambda b: b.__name__)
async def test_assigned_reviewers(db: Db, build: Callable[[], Awaitable[Scenario]]) -> None:
    scenario = await build()
    levels = await get_eligible_reviewers_by_level(scenario.request)
    assert (levels[0].owner_level if levels else None) == scenario.expected_level
    assert _names(scenario, await get_assigned_reviewers(scenario.request)) == scenario.expected_reviewers


@pytest.mark.parametrize("build", SCENARIOS, ids=lambda b: b.__name__)
async def test_eligible_reviewers_by_level(db: Db, build: Callable[[], Awaitable[Scenario]]) -> None:
    scenario = await build()
    levels = await get_eligible_reviewers_by_level(scenario.request)
    assert [(level.owner_level, _names(scenario, level.reviewers)) for level in levels] == scenario.expected_by_level


async def test_close_notification_uses_reviewers_assigned_before_approval(db: Db, mocker: MockerFixture) -> None:
    """Approving a role request for ownership makes the role's members owners of
    the group; the close notification still goes to the reviewers assigned
    while the request was open."""
    from api.operations import ApproveRoleRequest
    from api.plugins import get_notification_hook
    from api.services import okta

    u = await _users("requester", "app_owner", "role_member")
    app, _ = await _app(u["app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    req = await _role_request(u, group, role_members=("role_member",), ownership=True)

    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "add_user_to_group")
    hook = get_notification_hook()
    completed = mocker.patch.object(hook, "access_role_request_completed")

    await ApproveRoleRequest(role_request=req, approver_user=u["app_owner"]).execute()

    assert completed.call_count == 1
    _, kwargs = completed.call_args
    assert {user.id for user in kwargs["approvers"]} == {u["app_owner"].id}


async def test_modify_group_users_batch_close_notifications_use_reviewers_assigned_before_the_batch(
    db: Db, mocker: MockerFixture
) -> None:
    """Adding two requesters as owners in one call makes each an owner before
    either notification goes out; each still goes to the app owners, who were
    assigned while the requests were open, not to the other new owner."""
    from api.operations import ModifyGroupUsers
    from api.plugins import get_notification_hook
    from api.services import okta

    u = await _users("app_owner", "requester1", "requester2")
    app, _ = await _app(u["app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    requests = [
        await AccessRequestFactory.create_async(
            requester_user_id=u[name].id, requested_group_id=group.id, request_ownership=True
        )
        for name in ("requester1", "requester2")
    ]

    mocker.patch.object(okta, "add_owner_to_group")
    completed = mocker.patch.object(get_notification_hook(), "access_request_completed")

    await ModifyGroupUsers(
        group=group.id,
        owners_to_add=[u["requester1"].id, u["requester2"].id],
        current_user_id=u["app_owner"].id,
    ).execute()

    assert completed.call_count == 2
    notified = {call.kwargs["access_request"].id: call.kwargs["approvers"] for call in completed.call_args_list}
    assert set(notified) == {request.id for request in requests}
    for approvers in notified.values():
        assert {user.id for user in approvers} == {u["app_owner"].id}


async def test_modify_group_users_snapshots_only_requests_it_can_fulfil(db: Db, mocker: MockerFixture) -> None:
    """Adding a member to a role snapshots the member's requests for the role and
    the groups the role is associated with, not their other pending requests."""
    import api.operations.modify_group_users as modify_group_users
    from api.operations import ModifyGroupUsers
    from api.services import okta

    u = await _users("requester", "group_owner")
    role = await RoleGroupFactory.create_async()
    associated = await OktaGroupFactory.create_async()
    unrelated = await OktaGroupFactory.create_async()
    await _own(u["group_owner"], associated)
    await RoleGroupMapFactory.create_async(role_group_id=role.id, group_id=associated.id, is_owner=False)
    fulfillable = await AccessRequestFactory.create_async(
        requester_user_id=u["requester"].id, requested_group_id=associated.id
    )
    await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=unrelated.id)

    mocker.patch.object(okta, "add_user_to_group")
    snapshot = mocker.spy(modify_group_users, "snapshot_assigned_reviewers")

    await ModifyGroupUsers(group=role.id, members_to_add=[u["requester"].id], current_user_id=u["admin"].id).execute()

    assert [request.id for request in snapshot.call_args.args[0]] == [fulfillable.id]


@pytest.mark.parametrize("operation", ["ModifyGroupUsers", "ModifyRoleGroups"])
async def test_close_notification_computes_reviewers_for_a_request_missing_from_the_snapshot(
    db: Db, mocker: MockerFixture, operation: str
) -> None:
    """A request the snapshot misses, such as one opened after it was taken, has
    its reviewers computed when the operation closes it."""
    import api.operations.modify_group_users as modify_group_users
    import api.operations.modify_role_groups as modify_role_groups
    from api.operations import ModifyGroupUsers, ModifyRoleGroups
    from api.plugins import get_notification_hook
    from api.services import okta

    u = await _users("requester", "group_owner", "role_member")
    group = await OktaGroupFactory.create_async()
    await _own(u["group_owner"], group)
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")
    hook = get_notification_hook()

    if operation == "ModifyGroupUsers":
        mocker.patch.object(modify_group_users, "snapshot_assigned_reviewers", return_value={})
        completed = mocker.patch.object(hook, "access_request_completed")
        await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
        await ModifyGroupUsers(
            group=group.id, members_to_add=[u["requester"].id], current_user_id=u["group_owner"].id
        ).execute()
    else:
        mocker.patch.object(modify_role_groups, "snapshot_assigned_reviewers", return_value={})
        completed = mocker.patch.object(hook, "access_role_request_completed")
        role_request = await _role_request(u, group, role_members=("role_member",))
        await ModifyRoleGroups(
            role_group=role_request.requester_role_id,
            groups_to_add=[group.id],
            current_user_id=u["group_owner"].id,
        ).execute()

    assert completed.call_count == 1
    assert {user.id for user in completed.call_args.kwargs["approvers"]} == {u["group_owner"].id}


async def test_delete_group_close_notification_uses_reviewers_assigned_before_deletion(
    db: Db, mocker: MockerFixture
) -> None:
    """Deleting an app group closes its pending access requests; the close
    notification goes to the app owners assigned while the request was open,
    though the deleted group no longer routes to them."""
    from api.operations import DeleteGroup
    from api.plugins import get_notification_hook
    from api.services import okta

    u = await _users("requester", "app_owner")
    app, _ = await _app(u["app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)

    mocker.patch.object(okta, "delete_group")
    completed = mocker.patch.object(get_notification_hook(), "access_request_completed")

    await DeleteGroup(group=group.id).execute()

    assert completed.call_count == 1
    assert completed.call_args.kwargs["access_request"].id == req.id
    assert {user.id for user in completed.call_args.kwargs["approvers"]} == {u["app_owner"].id}


async def test_delete_role_close_notification_uses_reviewers_assigned_before_deletion(
    db: Db, mocker: MockerFixture
) -> None:
    """Deleting the requesting role ends its memberships, which would lift the
    tag block on a group owner who is a role member; the close notification
    still goes to the Access admins, who were assigned while it was open."""
    from api.operations import DeleteGroup
    from api.plugins import get_notification_hook

    u = await _users("requester", "blocked_owner")
    group = await OktaGroupFactory.create_async()
    await _own(u["blocked_owner"], group)
    tag = await _tag(Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY)
    await OktaGroupTagMapFactory.create_async(group_id=group.id, tag_id=tag.id)
    req = await _role_request(u, group, role_members=("blocked_owner",))

    completed = mocker.patch.object(get_notification_hook(), "access_role_request_completed")

    await DeleteGroup(group=req.requester_role_id, sync_to_okta=False).execute()

    assert completed.call_count == 1
    assert {user.id for user in completed.call_args.kwargs["approvers"]} == {u["admin"].id}


async def test_delete_app_close_notifications_use_reviewers_assigned_before_deletion(
    db: Db, mocker: MockerFixture
) -> None:
    """Deleting an app deletes its owners group too; the close notification for
    a pending request on another of its groups still goes to the app owners."""
    from api.operations import DeleteApp
    from api.plugins import get_notification_hook
    from api.services import okta

    u = await _users("requester", "app_owner")
    app, _ = await _app(u["app_owner"])
    group = await AppGroupFactory.create_async(app_id=app.id, is_owner=False)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)

    mocker.patch.object(okta, "delete_group")
    completed = mocker.patch.object(get_notification_hook(), "access_request_completed")

    await DeleteApp(app=app.id).execute()

    assert completed.call_count == 1
    assert completed.call_args.kwargs["access_request"].id == req.id
    assert {user.id for user in completed.call_args.kwargs["approvers"]} == {u["app_owner"].id}


async def test_unmanage_group_close_notification_uses_reviewers_assigned_before_unmanaging(
    db: Db, mocker: MockerFixture
) -> None:
    """Unmanaging a group ends ownership granted through a role; the close
    notification goes to the role member who owned the group while the request
    was open."""
    from api.extensions import db as ext_db
    from api.operations import UnmanageGroup
    from api.plugins import get_notification_hook

    u = await _users("requester", "role_owner")
    group = await OktaGroupFactory.create_async()
    role = await RoleGroupFactory.create_async()
    await _join(u["role_owner"], role)
    role_map = await RoleGroupMapFactory.create_async(role_group_id=role.id, group_id=group.id, is_owner=True)
    await _own(u["role_owner"], group, role_group_map_id=role_map.id)
    req = await AccessRequestFactory.create_async(requester_user_id=u["requester"].id, requested_group_id=group.id)
    assert [user.id for user in await get_assigned_reviewers(req)] == [u["role_owner"].id]

    group.is_managed = False
    await ext_db.session.commit()
    completed = mocker.patch.object(get_notification_hook(), "access_request_completed")

    await UnmanageGroup(group=group.id).execute()

    assert completed.call_count == 1
    assert {user.id for user in completed.call_args.kwargs["approvers"]} == {u["role_owner"].id}


_LIST_ROUTE = {AccessRequest: "access_requests", RoleRequest: "role_requests", GroupRequest: "group_requests"}


async def _assigned_via_list(
    client: AsyncClient, url_for: Callable[..., str], scenario: Scenario, user: OktaUser
) -> bool:
    """Whether the request shows in `user`'s "Assigned to Me" list."""
    rep = await client.get(
        url_for(_LIST_ROUTE[type(scenario.request)]), params={"assignee_user_id": user.id, "size": 100}
    )
    assert rep.status_code == 200
    return scenario.request.id in {item["id"] for item in rep.json()["items"]}


_RESOLVE_ROUTE = {
    AccessRequest: ("access_request_by_id_put", "access_request_id", ResolveAccessRequestBodyFactory),
    RoleRequest: ("role_request_by_id_put", "role_request_id", ResolveRoleRequestBodyFactory),
    GroupRequest: ("group_request_by_id_put", "group_request_id", ResolveGroupRequestBodyFactory),
}


async def _permitted_by_resolve_routes(
    app: FastAPI, client: AsyncClient, url_for: Callable[..., str], scenario: Scenario
) -> set[str]:
    """The scenario users the resolve route lets approve the request, plus the role approval's self-add check."""
    request = scenario.request
    route, param, body_factory = _RESOLVE_ROUTE[type(request)]
    url = url_for(route, **{param: request.id})
    body = body_factory.json(approved=True, reason="approve")
    # Read everything up front: a refused call rolls back the shared session,
    # which expires the objects this loop would otherwise read.
    users = [(name, user.id, user.email) for name, user in scenario.users.items()]
    role_check = (
        (request.requester_role_id, request.requested_group_id, request.request_ownership)
        if isinstance(request, RoleRequest)
        else None
    )
    permitted: set[str] = set()
    prev_email = app.state.current_user_email
    try:
        for user_name, user_id, email in users:
            app.state.current_user_email = email
            rep = await client.put(url, json=body)
            # 404 is the authentication dependency refusing a deleted user.
            assert rep.status_code in {403, 404, 409}, (user_name, rep.status_code, rep.text)
            if rep.status_code != 409:
                continue
            if role_check is not None:
                role_id, group_id, ownership = role_check
                valid, _ = await CheckForSelfAdd(
                    group=role_id,
                    current_user=user_id,
                    members_to_add=[] if ownership else [group_id],
                    owners_to_add=[group_id] if ownership else [],
                ).execute_for_role()
                if not valid:
                    continue
            permitted.add(user_name)
    finally:
        app.state.current_user_email = prev_email
    return permitted


@pytest.mark.parametrize("build", SCENARIOS, ids=lambda b: b.__name__)
async def test_reviewer_directions_agree(
    db: Db,
    app: FastAPI,
    client: AsyncClient,
    url_for: Callable[..., str],
    build: Callable[[], Awaitable[Scenario]],
) -> None:
    """Assigned by `get_assigned_reviewers` exactly when the "Assigned to Me"
    list shows the request; listed by `get_eligible_reviewers_by_level`
    exactly when permitted to approve; and the first level listed is the
    assigned one."""
    scenario = await build()
    assigned_names = _names(scenario, await get_assigned_reviewers(scenario.request))
    by_level = [_names(scenario, level.reviewers) for level in await get_eligible_reviewers_by_level(scenario.request)]
    assert (by_level[0] if by_level else set()) == assigned_names
    eligible = {name for names in by_level for name in names}
    # Drop identity-map state so the routes read the database, then reload the
    # objects this test reads directly.
    db.session.expire_all()
    for obj in (scenario.request, *scenario.users.values()):
        await db.session.refresh(obj)
    for name, user in scenario.users.items():
        assert (name in assigned_names) == await _assigned_via_list(client, url_for, scenario, user), name
    # Each resolve route checks permission before it checks that the request is
    # pending, so on a resolved request it answers 403 to a user it refuses and
    # 409 to one it permits, without approving anything.
    scenario.request.resolved_at = datetime.now(timezone.utc)
    await db.session.commit()
    assert eligible == await _permitted_by_resolve_routes(app, client, url_for, scenario)


_REVIEWERS_ROUTE = {
    AccessRequest: ("access_request_reviewers", "access_request_id"),
    RoleRequest: ("role_request_reviewers", "role_request_id"),
    GroupRequest: ("group_request_reviewers", "group_request_id"),
}


@pytest.mark.parametrize(
    "build",
    [
        access_requester_is_sole_group_owner_and_an_app_owner,
        role_tag_blocks_one_group_owner,
        group_app_group_with_app_owners,
    ],
    ids=lambda b: b.__name__,
)
async def test_reviewers_route(
    db: Db, client: AsyncClient, url_for: Callable[..., str], build: Callable[[], Awaitable[Scenario]]
) -> None:
    scenario = await build()
    name, param = _REVIEWERS_ROUTE[type(scenario.request)]
    rep = await client.get(url_for(name, **{param: scenario.request.id}))
    assert rep.status_code == 200
    body = rep.json()
    by_id = {user.id: n for n, user in scenario.users.items()}
    assert [
        (level["owner_level"], {by_id[r["id"]] for r in level["reviewers"]}) for level in body["reviewers_by_level"]
    ] == scenario.expected_by_level


@pytest.mark.parametrize(
    "build",
    [
        access_requester_is_sole_group_owner_and_an_app_owner,
        role_tag_blocks_one_group_owner,
        group_app_group_with_app_owners,
    ],
    ids=lambda b: b.__name__,
)
async def test_reviewers_route_unknown_id(
    db: Db, client: AsyncClient, url_for: Callable[..., str], build: Callable[[], Awaitable[Scenario]]
) -> None:
    """The same route that answers for a known request answers 404 for an unknown id."""
    scenario = await build()
    name, param = _REVIEWERS_ROUTE[type(scenario.request)]
    assert (await client.get(url_for(name, **{param: scenario.request.id}))).status_code == 200
    rep = await client.get(url_for(name, **{param: "missing"}))
    assert rep.status_code == 404
    assert rep.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("model", [AccessRequest, RoleRequest, GroupRequest], ids=lambda m: m.__name__)
async def test_reviewers_route_requires_authentication(
    app: FastAPI, client: AsyncClient, url_for: Callable[..., str], model: type
) -> None:
    """The route declares `CurrentUserId`, so a failing authentication dependency rejects the call."""

    def reject() -> str:
        raise HTTPException(status_code=401, detail="Unauthenticated")

    app.dependency_overrides[get_current_user_id] = reject
    name, param = _REVIEWERS_ROUTE[model]
    rep = await client.get(url_for(name, **{param: "any"}))
    assert rep.status_code == 401
