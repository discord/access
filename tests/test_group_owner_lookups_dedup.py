"""A user can hold two concurrent active OktaUserGroupMember owner rows for the
same group at once -- a direct grant plus one via a role mapping, per the
access-grant-precedence rules -- and those rows are never collapsed into one.
The approver lookups used to notify a single access request must still return
such a user once, not once per row, or the notification plugin sends one DM
per list entry for what is really one owner.
"""

from sqlalchemy import select

from api.models import App, AppGroup, OktaUser, RoleGroup
from api.models.app_group import get_access_owners, get_app_managers
from api.models.okta_group import get_group_managers
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserGroupMemberFactory,
    RoleGroupFactory,
    RoleGroupMapFactory,
)


async def _double_owner(group_id: str, owner: OktaUser, role: RoleGroup) -> None:
    """Grant `owner` ownership of `group_id` both directly and via `role`."""
    role_group_map = await RoleGroupMapFactory.create_async(
        role_group_id=role.id,
        group_id=group_id,
        is_owner=True,
    )
    await OktaUserGroupMemberFactory.create_async(
        user_id=owner.id,
        group_id=group_id,
        is_owner=True,
        role_group_map_id=role_group_map.id,
    )
    await OktaUserGroupMemberFactory.create_async(
        user_id=owner.id,
        group_id=group_id,
        is_owner=True,
        role_group_map_id=None,
    )


async def test_get_group_managers_dedupes_direct_and_role_ownership(db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()

    group = await OktaGroupFactory.create_async()
    role = await RoleGroupFactory.create_async()
    await _double_owner(group.id, user, role)

    managers = await get_group_managers(group.id)

    assert [m.id for m in managers] == [user.id]


async def test_get_app_managers_dedupes_direct_and_role_ownership(db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()

    app = await AppFactory.create_async()
    owner_group = await AppGroupFactory.create_async(app_id=app.id, is_owner=True)
    role = await RoleGroupFactory.create_async()
    await _double_owner(owner_group.id, user, role)

    managers = await get_app_managers(app.id)

    assert [m.id for m in managers] == [user.id]


async def test_get_access_owners_dedupes_direct_and_role_membership(db, user: OktaUser) -> None:
    db.session.add(user)
    await db.session.commit()

    # The `db` fixture already seeds the bootstrap Access app + its owner
    # group; reuse it rather than creating a second app with the reserved name.
    access_app = (await db.session.scalars(select(App).where(App.name == App.ACCESS_APP_RESERVED_NAME))).one()
    owner_group = (
        await db.session.scalars(
            select(AppGroup).where(AppGroup.app_id == access_app.id).where(AppGroup.is_owner.is_(True))
        )
    ).one()
    role = await RoleGroupFactory.create_async()
    role_group_map = await RoleGroupMapFactory.create_async(
        role_group_id=role.id,
        group_id=owner_group.id,
        is_owner=False,
    )
    await OktaUserGroupMemberFactory.create_async(
        user_id=user.id,
        group_id=owner_group.id,
        is_owner=False,
        role_group_map_id=role_group_map.id,
    )
    await OktaUserGroupMemberFactory.create_async(
        user_id=user.id,
        group_id=owner_group.id,
        is_owner=False,
        role_group_map_id=None,
    )

    owners = await get_access_owners()
    owner_ids = [o.id for o in owners]

    # The bootstrap admin (seeded by the `db` fixture) is also a member of
    # this group, so `user` is one of (at least) two owners -- the point is
    # that `user` appears exactly once despite its two active membership rows.
    assert owner_ids.count(user.id) == 1
