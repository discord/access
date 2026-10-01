"""Who reviews each kind of request; see api/models/request_reviewers.py."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select

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
    get_possible_reviewers_by_level,
)
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
    RoleRequestFactory,
    TagFactory,
)


@dataclass
class Scenario:
    """A persisted request plus the names of the users it involves."""

    request: AccessRequest | RoleRequest | GroupRequest
    users: dict[str, OktaUser]
    expected_level: Optional[OwnerLevel]
    expected_reviewers: set[str]
    expected_possible: list[tuple[OwnerLevel, set[str]]] = field(default_factory=list)


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
        [("group_owners", {"requester"}), ("access_admins", {"admin"})],
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
        [("group_owners", set()), ("app_owners", {"app_owner"}), ("access_admins", {"admin"})],
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
        [("group_owners", {"requester"}), ("app_owners", {"app_owner", "requester"}), ("access_admins", {"admin"})],
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
        [("group_owners", set()), ("app_owners", set()), ("access_admins", {"admin"})],
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
        [("group_owners", set()), ("access_admins", {"admin", "admin2"})],
    )


async def access_nobody_eligible() -> Scenario:
    u = await _users()
    group = await OktaGroupFactory.create_async()
    req = await AccessRequestFactory.create_async(requester_user_id=u["admin"].id, requested_group_id=group.id)
    return Scenario(req, u, None, set(), [("group_owners", set()), ("access_admins", {"admin"})])


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
        [("group_owners", set()), ("access_admins", {"admin"})],
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
        [("group_owners", set()), ("access_admins", {"admin"})],
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
        [("group_owners", {"blocked_owner", "owner"}), ("access_admins", {"admin"})],
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
        [("group_owners", {"blocked_owner"}), ("access_admins", {"admin"})],
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
        [("group_owners", {"blocked_owner"}), ("access_admins", {"admin"})],
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
        [("group_owners", {"blocked_owner"}), ("app_owners", set()), ("access_admins", {"admin"})],
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
        [("group_owners", set()), ("app_owners", {"blocked_app_owner"}), ("access_admins", {"admin"})],
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
        [("group_owners", {"admin"}), ("access_admins", {"admin"})],
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
        [("app_owners", set()), ("access_admins", {"admin"})],
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
        [("app_owners", {"requester"}), ("access_admins", {"admin"})],
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
    assigned = await get_assigned_reviewers(scenario.request)
    assert assigned.owner_level == scenario.expected_level
    assert _names(scenario, assigned.reviewers) == scenario.expected_reviewers


@pytest.mark.parametrize("build", SCENARIOS, ids=lambda b: b.__name__)
async def test_possible_reviewers_by_level(db: Db, build: Callable[[], Awaitable[Scenario]]) -> None:
    scenario = await build()
    levels = await get_possible_reviewers_by_level(scenario.request)
    assert [(level.owner_level, _names(scenario, level.reviewers)) for level in levels] == scenario.expected_possible


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
