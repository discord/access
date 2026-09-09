"""Tests for `api.redundant_access` and the `prune-redundant-direct-access` CLI command."""

from datetime import UTC, datetime, timedelta
from typing import Any

import click
import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture
from sqlalchemy import select

from api.cli import cli, prune_redundant_direct_access_command
from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.operations import ModifyGroupUsers
from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginSpec,
    hookimpl,
)
from api.redundant_access import (
    AccessTarget,
    FilterResolutionError,
    PruneOutcome,
    RedundantGrant,
    _later,
    find_redundant_grants,
    prune_redundant_direct_access,
    resolve_group_ids,
    resolve_user_ids,
)
from api.services import okta
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserFactory,
    OktaUserGroupMemberFactory,
    RoleGroupFactory,
    RoleGroupMapFactory,
)
from tests.helpers import db_count

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
IN_30_DAYS = NOW + timedelta(days=30)
IN_60_DAYS = NOW + timedelta(days=60)


def _grant(direct: datetime | None, role: datetime | None) -> RedundantGrant:
    """A `RedundantGrant` whose identity fields are irrelevant to the assertion."""
    return RedundantGrant(
        user_id="u1",
        user_email="alice@example.com",
        group_id="g1",
        group_name="Payments",
        is_owner=False,
        latest_direct_ended_at=direct,
        latest_role_ended_at=role,
    )


class TestAccessTarget:
    def test_members_covers_only_membership(self) -> None:
        assert AccessTarget.MEMBERS.is_owner_values() == (False,)

    def test_owners_covers_only_ownership(self) -> None:
        assert AccessTarget.OWNERS.is_owner_values() == (True,)

    def test_both_covers_both_access_types(self) -> None:
        assert set(AccessTarget.BOTH.is_owner_values()) == {False, True}

    def test_values_match_the_cli_choice_strings(self) -> None:
        # The Click option converts its raw string straight into this enum.
        assert AccessTarget("members") is AccessTarget.MEMBERS
        assert AccessTarget("owners") is AccessTarget.OWNERS
        assert AccessTarget("both") is AccessTarget.BOTH


class TestLater:
    def test_none_is_indefinite_and_wins_from_either_side(self) -> None:
        assert _later(None, IN_30_DAYS) is None
        assert _later(IN_30_DAYS, None) is None
        assert _later(None, None) is None

    def test_returns_the_later_of_two_dates(self) -> None:
        assert _later(IN_30_DAYS, IN_60_DAYS) == IN_60_DAYS
        assert _later(IN_60_DAYS, IN_30_DAYS) == IN_60_DAYS


class TestShortensAccess:
    def test_both_indefinite_does_not_shorten(self) -> None:
        assert _grant(None, None).shortens_access is False

    def test_dated_direct_under_indefinite_role_does_not_shorten(self) -> None:
        assert _grant(IN_30_DAYS, None).shortens_access is False

    def test_indefinite_direct_under_dated_role_shortens(self) -> None:
        assert _grant(None, IN_30_DAYS).shortens_access is True

    def test_shorter_direct_does_not_shorten(self) -> None:
        assert _grant(IN_30_DAYS, IN_60_DAYS).shortens_access is False

    def test_longer_direct_shortens(self) -> None:
        assert _grant(IN_60_DAYS, IN_30_DAYS).shortens_access is True

    def test_equal_end_dates_do_not_shorten(self) -> None:
        # The guard is `<=`: an identical end date takes nothing away.
        assert _grant(IN_30_DAYS, IN_30_DAYS).shortens_access is False


async def _direct_grant(
    *, user: OktaUser, group: OktaGroup, is_owner: bool = False, ended_at: datetime | None = None
) -> OktaUserGroupMember:
    """Persist a direct grant: an active membership row with no role behind it."""
    return await OktaUserGroupMemberFactory.create_async(
        user_id=user.id, group_id=group.id, is_owner=is_owner, ended_at=ended_at
    )


async def _role_grant(
    *,
    user: OktaUser,
    group: OktaGroup,
    role_group: RoleGroup,
    is_owner: bool = False,
    ended_at: datetime | None = None,
) -> OktaUserGroupMember:
    """Persist role-derived coverage: a RoleGroupMap plus the membership row it
    justifies.

    The row's own `ended_at` is what the operations layer already coalesces from
    the role membership and the mapping, so tests set it directly rather than
    reconstructing that arithmetic. The user's membership in the role group
    itself is not created: discovery keys off `role_group_map_id` on the derived
    row, so an extra row would only add noise.
    """
    role_map = await RoleGroupMapFactory.create_async(role_group_id=role_group.id, group_id=group.id, is_owner=is_owner)
    return await OktaUserGroupMemberFactory.create_async(
        user_id=user.id,
        group_id=group.id,
        is_owner=is_owner,
        role_group_map_id=role_map.id,
        ended_at=ended_at,
    )


class TestFindRedundantGrants:
    async def test_direct_and_role_coverage_is_a_candidate(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        grants = await find_redundant_grants(target=AccessTarget.BOTH)

        assert len(grants) == 1
        assert grants[0].user_id == user.id
        assert grants[0].user_email == user.email
        assert grants[0].group_id == okta_group.id
        assert grants[0].group_name == okta_group.name
        assert grants[0].is_owner is False
        assert grants[0].latest_direct_ended_at is None
        assert grants[0].latest_role_ended_at is None

    async def test_direct_grant_without_role_coverage_is_not_a_candidate(
        self, db: Db, user: OktaUser, okta_group: OktaGroup
    ) -> None:
        db.session.add_all([user, okta_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_role_coverage_without_direct_grant_is_not_a_candidate(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_expired_direct_grant_is_not_a_candidate(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group, ended_at=datetime.now(UTC) - timedelta(days=1))
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_expired_role_coverage_is_not_a_candidate(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(
            user=user, group=okta_group, role_group=role_group, ended_at=datetime.now(UTC) - timedelta(days=1)
        )
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_membership_and_ownership_are_separate_triples(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # A direct membership covered by role *ownership* is not redundant: the
        # access types grant different things.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group, is_owner=False)
        await _role_grant(user=user, group=okta_group, role_group=role_group, is_owner=True)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_target_returns_only_memberships_or_only_ownerships(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        for is_owner in (False, True):
            await _direct_grant(user=user, group=okta_group, is_owner=is_owner)
            await _role_grant(user=user, group=okta_group, role_group=role_group, is_owner=is_owner)
        await db.session.commit()

        assert len(await find_redundant_grants(target=AccessTarget.BOTH)) == 2
        members_only = await find_redundant_grants(target=AccessTarget.MEMBERS)
        assert [g.is_owner for g in members_only] == [False]
        owners_only = await find_redundant_grants(target=AccessTarget.OWNERS)
        assert [g.is_owner for g in owners_only] == [True]

    async def test_multiple_covering_roles_take_the_longest_coverage(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # An indefinite role covers the group even though another covering role
        # is expiring, so `latest_role_ended_at` is indefinite.
        second_role = RoleGroupFactory.build()
        db.session.add_all([user, okta_group, role_group, second_role])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(
            user=user, group=okta_group, role_group=role_group, ended_at=datetime.now(UTC) + timedelta(days=30)
        )
        await _role_grant(user=user, group=okta_group, role_group=second_role, ended_at=None)
        await db.session.commit()

        grants = await find_redundant_grants(target=AccessTarget.BOTH)

        assert len(grants) == 1
        assert grants[0].latest_role_ended_at is None

    async def test_multiple_direct_rows_take_the_longest_grant(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # Grant precedence normally collapses these into one row, but the guard
        # must reflect the longest of whatever is actually active.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        soon = datetime.now(UTC) + timedelta(days=10)
        later = datetime.now(UTC) + timedelta(days=45)
        await _direct_grant(user=user, group=okta_group, ended_at=soon)
        await _direct_grant(user=user, group=okta_group, ended_at=later)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        grants = await find_redundant_grants(target=AccessTarget.BOTH)

        assert len(grants) == 1
        assert grants[0].latest_direct_ended_at is not None
        # NaiveUTCDateTime normalizes an aware value to naive UTC on bind, so what
        # comes back is naive. The tolerance keeps the assertion about which of the
        # two rows won rather than about round-trip precision.
        assert abs(grants[0].latest_direct_ended_at.replace(tzinfo=UTC) - later) < timedelta(seconds=1)

    async def test_soft_deleted_user_is_excluded(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        user.deleted_at = datetime.now(UTC)
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_soft_deleted_group_is_excluded(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        okta_group.deleted_at = datetime.now(UTC)
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_group_ids_narrows_to_named_groups(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # A second, genuine candidate in another group must be excluded once
        # `group_ids` narrows to the first group only.
        other_group = OktaGroupFactory.build()
        db.session.add_all([user, okta_group, other_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await _direct_grant(user=user, group=other_group)
        await _role_grant(user=user, group=other_group, role_group=role_group)
        await db.session.commit()

        assert len(await find_redundant_grants(target=AccessTarget.BOTH)) == 2

        grants = await find_redundant_grants(target=AccessTarget.BOTH, group_ids={okta_group.id})

        assert len(grants) == 1
        assert grants[0].group_id == okta_group.id

    async def test_group_ids_empty_set_excludes_everything(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # An empty set narrows to nothing, distinct from `None`'s "no narrowing".
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH, group_ids=set()) == []

    async def test_user_ids_narrows_to_named_users(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # A second, genuine candidate for another user must be excluded once
        # `user_ids` narrows to the first user only.
        other_user = OktaUserFactory.build()
        db.session.add_all([user, other_user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await _direct_grant(user=other_user, group=okta_group)
        await _role_grant(user=other_user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert len(await find_redundant_grants(target=AccessTarget.BOTH)) == 2

        grants = await find_redundant_grants(target=AccessTarget.BOTH, user_ids={user.id})

        assert len(grants) == 1
        assert grants[0].user_id == user.id

    async def test_user_ids_empty_set_excludes_everything(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # An empty set narrows to nothing, distinct from `None`'s "no narrowing".
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH, user_ids=set()) == []

    async def test_role_coverage_in_another_group_does_not_leak_into_a_candidate(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # Two users, two groups, and each user additionally holds role-only
        # coverage in the *other* user's group. Only the two triples that hold
        # both a direct grant and role coverage are candidates, and each must
        # carry its own group's role end date rather than the one next door.
        user_b = OktaUserFactory.build()
        group_b = OktaGroupFactory.build()
        db.session.add_all([user, user_b, okta_group, group_b, role_group])
        await db.session.commit()

        # Genuine candidate: (user, okta_group, member).
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        # Role-only coverage for `user` in `group_b`: no direct grant, so not a
        # candidate, and its end date must not reach the candidate above.
        await _role_grant(user=user, group=group_b, role_group=role_group, ended_at=IN_30_DAYS)

        # Genuine candidate: (user_b, group_b, member), with the mirror image of
        # the same arrangement.
        await _direct_grant(user=user_b, group=group_b)
        await _role_grant(user=user_b, group=group_b, role_group=role_group)
        await _role_grant(user=user_b, group=okta_group, role_group=role_group, ended_at=IN_60_DAYS)
        await db.session.commit()

        grants = await find_redundant_grants(target=AccessTarget.BOTH)

        assert len(grants) == 2
        by_user = {g.user_id: g for g in grants}
        assert by_user[user.id].group_id == okta_group.id
        assert by_user[user.id].latest_role_ended_at is None
        assert by_user[user_b.id].group_id == group_b.id
        assert by_user[user_b.id].latest_role_ended_at is None


class TestResolveGroupIds:
    async def test_no_filters_means_no_narrowing(self, db: Db) -> None:
        assert await resolve_group_ids((), ()) is None

    async def test_resolves_a_group_by_name_and_by_id(self, db: Db, okta_group: OktaGroup) -> None:
        db.session.add(okta_group)
        await db.session.commit()

        assert await resolve_group_ids((okta_group.name,), ()) == {okta_group.id}
        assert await resolve_group_ids((okta_group.id,), ()) == {okta_group.id}

    async def test_app_expands_to_its_active_app_groups(self, db: Db) -> None:
        test_app = AppFactory.build(name="Payments")
        db.session.add(test_app)
        await db.session.commit()
        first = AppGroupFactory.build(app_id=test_app.id, name="App-Payments-Admins")
        second = AppGroupFactory.build(app_id=test_app.id, name="App-Payments-Users")
        other_app = AppFactory.build(name="Billing")
        db.session.add_all([first, second, other_app])
        await db.session.commit()
        db.session.add(AppGroupFactory.build(app_id=other_app.id, name="App-Billing-Admins"))
        await db.session.commit()

        assert await resolve_group_ids((), ("Payments",)) == {first.id, second.id}

    async def test_group_and_app_filters_union(self, db: Db, okta_group: OktaGroup) -> None:
        test_app = AppFactory.build(name="Payments")
        db.session.add_all([okta_group, test_app])
        await db.session.commit()
        app_group = AppGroupFactory.build(app_id=test_app.id, name="App-Payments-Admins")
        db.session.add(app_group)
        await db.session.commit()

        assert await resolve_group_ids((okta_group.name,), ("Payments",)) == {okta_group.id, app_group.id}

    async def test_app_with_no_active_groups_narrows_to_nothing(self, db: Db) -> None:
        # Resolved, but contributes no groups. An empty set is not None: it means
        # the sweep is narrowed to nothing, which is the honest answer.
        db.session.add(AppFactory.build(name="Empty"))
        await db.session.commit()

        assert await resolve_group_ids((), ("Empty",)) == set()

    async def test_unknown_group_raises(self, db: Db) -> None:
        with pytest.raises(FilterResolutionError, match="No active group matches 'Nope'"):
            await resolve_group_ids(("Nope",), ())

    async def test_unknown_app_raises(self, db: Db) -> None:
        with pytest.raises(FilterResolutionError, match="No active app matches 'Nope'"):
            await resolve_group_ids((), ("Nope",))

    async def test_soft_deleted_group_does_not_resolve(self, db: Db, okta_group: OktaGroup) -> None:
        okta_group.deleted_at = datetime.now(UTC)
        db.session.add(okta_group)
        await db.session.commit()

        with pytest.raises(FilterResolutionError):
            await resolve_group_ids((okta_group.name,), ())

    async def test_soft_deleted_app_group_is_not_expanded(self, db: Db) -> None:
        test_app = AppFactory.build(name="Payments")
        db.session.add(test_app)
        await db.session.commit()
        live = AppGroupFactory.build(app_id=test_app.id, name="App-Payments-Admins")
        gone = AppGroupFactory.build(app_id=test_app.id, name="App-Payments-Old", deleted_at=datetime.now(UTC))
        db.session.add_all([live, gone])
        await db.session.commit()

        assert await resolve_group_ids((), ("Payments",)) == {live.id}


class TestResolveUserIds:
    async def test_no_filters_means_no_narrowing(self, db: Db) -> None:
        assert await resolve_user_ids(()) is None

    async def test_resolves_by_email_case_insensitively_and_by_id(self, db: Db, user: OktaUser) -> None:
        db.session.add(user)
        await db.session.commit()

        assert await resolve_user_ids((user.email,)) == {user.id}
        assert await resolve_user_ids((user.email.upper(),)) == {user.id}
        assert await resolve_user_ids((user.id,)) == {user.id}

    async def test_unknown_user_raises(self, db: Db) -> None:
        with pytest.raises(FilterResolutionError, match="No active user matches 'nobody@example.com'"):
            await resolve_user_ids(("nobody@example.com",))

    async def test_soft_deleted_user_does_not_resolve(self, db: Db, user: OktaUser) -> None:
        user.deleted_at = datetime.now(UTC)
        db.session.add(user)
        await db.session.commit()

        with pytest.raises(FilterResolutionError):
            await resolve_user_ids((user.email,))

    async def test_email_matching_is_like_based_with_underscore_wildcard(self, db: Db) -> None:
        # The value goes into ILIKE unescaped, so `_` is a single-character
        # wildcard. This matches the id-or-email resolution idiom used across
        # the codebase in ~10 call sites. A caller wanting exact matching would
        # need `func.lower(...) == func.lower(...)` instead.
        user_with_x_in_email = OktaUserFactory.build(email="janexdoe@example.com")
        db.session.add(user_with_x_in_email)
        await db.session.commit()

        # Querying with `jane_doe` (underscore) matches `janexdoe` (x) via ILIKE.
        # This test fails if someone changes to exact matching.
        result_ids = await resolve_user_ids(("jane_doe@example.com",))

        assert result_ids == {user_with_x_in_email.id}


async def _active_direct_count(db: Db, *, user: OktaUser, group: OktaGroup, is_owner: bool = False) -> int:
    return await db_count(
        db.session,
        select(OktaUserGroupMember)
        .where(OktaUserGroupMember.user_id == user.id)
        .where(OktaUserGroupMember.group_id == group.id)
        .where(OktaUserGroupMember.is_owner.is_(is_owner))
        .where(OktaUserGroupMember.role_group_map_id.is_(None))
        .where(OktaUserGroupMember.ended_at.is_(None)),
    )


async def _active_role_derived_count(db: Db, *, user: OktaUser, group: OktaGroup) -> int:
    return await db_count(
        db.session,
        select(OktaUserGroupMember)
        .where(OktaUserGroupMember.user_id == user.id)
        .where(OktaUserGroupMember.group_id == group.id)
        .where(OktaUserGroupMember.role_group_map_id.isnot(None))
        .where(OktaUserGroupMember.ended_at.is_(None)),
    )


class _RecordingLifecyclePlugin:
    """A minimal app-group-lifecycle plugin that records `group_members_removed`
    calls, registered the way `tests/test_app_group_lifecycle_plugin.py`'s
    `test_plugin` fixture registers its own `DummyPlugin`."""

    ID = "prune_test_lifecycle_plugin"

    def __init__(self) -> None:
        self.members_removed_calls: list[tuple[str, list[str]]] = []

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        return AppGroupLifecyclePluginMetadata(
            id=self.ID, display_name="Prune Test Plugin", description="Records member-removal hook calls."
        )

    @hookimpl
    async def group_members_removed(self, ctx: Any, group: Any, members: list[OktaUser], plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        self.members_removed_calls.append((group.id, [m.id for m in members]))


@pytest.fixture
def lifecycle_plugin(db: Db, mocker: MockerFixture) -> Any:
    """Register `_RecordingLifecyclePlugin` as the app group lifecycle plugin hook
    relay, mirroring the registration in `tests/test_app_group_lifecycle_plugin.py`.
    """
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module

    plugin_instance = _RecordingLifecyclePlugin()
    pm = pluggy.PluginManager(plugin_module.app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)
    pm.register(plugin_instance, name=_RecordingLifecyclePlugin.ID)

    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    mocker.patch.object(plugin_module, "_cached_plugin_registry", None)

    yield plugin_instance

    plugin_module._cached_app_group_lifecycle_hook = None
    plugin_module._cached_plugin_registry = None


class TestPruneDriverAppGroupLifecycle:
    async def test_no_lifecycle_hook_fires_when_role_derived_access_survives(
        self, db: Db, user: OktaUser, role_group: RoleGroup, lifecycle_plugin: Any
    ) -> None:
        # Every other driver test uses `OktaGroupFactory`; an `AppGroup` is where
        # `ModifyGroupUsers` does its extra `joinedload(AppGroup.app)` /
        # `selectin_polymorphic` work and where the lifecycle-hook branch lives. The
        # hook must not fire: the role-derived row survives the prune, so the user
        # never loses all access to the group.
        test_app = AppFactory.build(name="PruneLifecycleApp", app_group_lifecycle_plugin=lifecycle_plugin.ID)
        db.session.add_all([user, role_group, test_app])
        await db.session.commit()
        app_group = AppGroupFactory.build(app_id=test_app.id, name=f"App-{test_app.name}-Admins")
        db.session.add(app_group)
        await db.session.commit()
        await _direct_grant(user=user, group=app_group)
        await _role_grant(user=user, group=app_group, role_group=role_group)
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        assert summary.removed == 1
        assert await _active_direct_count(db, user=user, group=app_group) == 0
        assert await _active_role_derived_count(db, user=user, group=app_group) == 1
        assert lifecycle_plugin.members_removed_calls == []


class TestPruneDriver:
    async def test_dry_run_reports_but_writes_nothing(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=True)

        assert summary.candidates == 1
        assert summary.removed == 1
        assert summary.decisions[0].outcome is PruneOutcome.REMOVED
        assert await _active_direct_count(db, user=user, group=okta_group) == 1

    async def test_apply_ends_the_direct_grant_and_keeps_the_role_grant(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        assert summary.removed == 1
        assert await _active_direct_count(db, user=user, group=okta_group) == 0
        assert await _active_role_derived_count(db, user=user, group=okta_group) == 1

    async def test_removal_leaves_no_ended_actor(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # The CLI has no acting user, matching how the syncer attributes its own
        # removals.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        direct = await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        await db.session.refresh(direct)
        assert direct.ended_at is not None
        assert direct.ended_actor_id is None

    async def test_guard_skips_a_direct_grant_that_outlives_the_role(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group, ended_at=None)
        await _role_grant(
            user=user, group=okta_group, role_group=role_group, ended_at=datetime.now(UTC) + timedelta(days=30)
        )
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        assert summary.candidates == 1
        assert summary.skipped == 1
        assert summary.removed == 0
        assert summary.decisions[0].outcome is PruneOutcome.SKIPPED_WOULD_SHORTEN
        assert await _active_direct_count(db, user=user, group=okta_group) == 1

    async def test_allow_shortening_removes_the_longer_direct_grant(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group, ended_at=None)
        await _role_grant(
            user=user, group=okta_group, role_group=role_group, ended_at=datetime.now(UTC) + timedelta(days=30)
        )
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False, allow_shortening=True)

        assert summary.removed == 1
        assert await _active_direct_count(db, user=user, group=okta_group) == 0

    async def test_okta_is_never_called(
        self, db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # The role-derived row survives every removal, so ModifyGroupUsers finds
        # other active access and leaves the external group membership alone.
        okta_group.is_managed = True
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()
        remove_member = mocker.patch.object(okta, "remove_user_from_group")
        remove_owner = mocker.patch.object(okta, "remove_owner_from_group")

        await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        assert remove_member.call_count == 0
        assert remove_owner.call_count == 0

    async def test_both_access_types_prune_in_one_pass(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        for is_owner in (False, True):
            await _direct_grant(user=user, group=okta_group, is_owner=is_owner)
            await _role_grant(user=user, group=okta_group, role_group=role_group, is_owner=is_owner)
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        assert summary.removed == 2
        assert await _active_direct_count(db, user=user, group=okta_group, is_owner=False) == 0
        assert await _active_direct_count(db, user=user, group=okta_group, is_owner=True) == 0

    async def test_target_members_leaves_ownership_alone(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        for is_owner in (False, True):
            await _direct_grant(user=user, group=okta_group, is_owner=is_owner)
            await _role_grant(user=user, group=okta_group, role_group=role_group, is_owner=is_owner)
        await db.session.commit()

        summary = await prune_redundant_direct_access(target=AccessTarget.MEMBERS, dry_run=False)

        assert summary.removed == 1
        assert await _active_direct_count(db, user=user, group=okta_group, is_owner=False) == 0
        assert await _active_direct_count(db, user=user, group=okta_group, is_owner=True) == 1

    async def test_a_failing_group_does_not_strand_the_others(
        self, db: Db, mocker: MockerFixture, user: OktaUser, role_group: RoleGroup
    ) -> None:
        first = OktaGroupFactory.build(name="AFirstGroup")
        second = OktaGroupFactory.build(name="BSecondGroup")
        db.session.add_all([user, role_group, first, second])
        await db.session.commit()
        for group in (first, second):
            await _direct_grant(user=user, group=group)
            await _role_grant(user=user, group=group, role_group=role_group)
        await db.session.commit()

        real_execute = ModifyGroupUsers.execute
        # Captured eagerly as a plain string: the driver's own rollback on the
        # first group's failure expires every ORM instance in the identity
        # map, `first` included, so reading `first.id` lazily inside the
        # closure on the second call would itself raise MissingGreenlet.
        first_id = first.id

        async def _fail_on_first(self: ModifyGroupUsers) -> Any:
            if self.group_id == first_id:
                raise RuntimeError("boom")
            return await real_execute(self)

        mocker.patch.object(ModifyGroupUsers, "execute", _fail_on_first)

        summary = await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        assert summary.failed == 1
        assert summary.removed == 1
        assert await _active_direct_count(db, user=user, group=second) == 0

    async def test_filters_narrow_the_sweep(self, db: Db, user: OktaUser, role_group: RoleGroup) -> None:
        wanted = OktaGroupFactory.build(name="Wanted")
        other = OktaGroupFactory.build(name="Other")
        db.session.add_all([user, role_group, wanted, other])
        await db.session.commit()
        for group in (wanted, other):
            await _direct_grant(user=user, group=group)
            await _role_grant(user=user, group=group, role_group=role_group)
        await db.session.commit()

        summary = await prune_redundant_direct_access(
            target=AccessTarget.BOTH, dry_run=False, group_filters=("Wanted",)
        )

        assert summary.candidates == 1
        assert await _active_direct_count(db, user=user, group=wanted) == 0
        assert await _active_direct_count(db, user=user, group=other) == 1

    async def test_unresolvable_filter_raises_before_writing(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        with pytest.raises(FilterResolutionError):
            await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False, group_filters=("Nope",))

        assert await _active_direct_count(db, user=user, group=okta_group) == 1

    async def test_app_filter_narrows_the_sweep(self, db: Db, user: OktaUser, role_group: RoleGroup) -> None:
        # `resolve_group_ids` is covered in isolation by `TestResolveGroupIds`; this
        # exercises `--app` through the driver end to end, the way
        # `test_filters_narrow_the_sweep` above does for `--group`.
        wanted_app = AppFactory.build(name="PruneWantedApp")
        other_app = AppFactory.build(name="PruneOtherApp")
        db.session.add_all([user, role_group, wanted_app, other_app])
        await db.session.commit()
        wanted_group = AppGroupFactory.build(app_id=wanted_app.id, name=f"App-{wanted_app.name}-Admins")
        other_group = AppGroupFactory.build(app_id=other_app.id, name=f"App-{other_app.name}-Admins")
        db.session.add_all([wanted_group, other_group])
        await db.session.commit()
        for group in (wanted_group, other_group):
            await _direct_grant(user=user, group=group)
            await _role_grant(user=user, group=group, role_group=role_group)
        await db.session.commit()

        summary = await prune_redundant_direct_access(
            target=AccessTarget.BOTH, dry_run=False, app_filters=(wanted_app.name,)
        )

        assert summary.candidates == 1
        assert await _active_direct_count(db, user=user, group=wanted_group) == 0
        assert await _active_direct_count(db, user=user, group=other_group) == 1

    async def test_audit_event_emitted_for_each_pruned_group(
        self,
        db: Db,
        caplog: pytest.LogCaptureFixture,
        user: OktaUser,
        okta_group: OktaGroup,
        role_group: RoleGroup,
    ) -> None:
        # The actor is NULL by design and a removal carries no reason field, so this
        # audit event is the only trace a prune run leaves behind.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        with caplog.at_level("INFO", logger="access.audit"):
            await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        audit_messages = [r.getMessage() for r in caplog.records if r.name == "access.audit"]
        assert any("GROUP_MODIFY_USER" in m for m in audit_messages), audit_messages
        assert any(okta_group.id in m for m in audit_messages), audit_messages

    async def test_surviving_role_derived_row_is_left_untouched(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # `_active_role_derived_count` above only pins that the row is still active;
        # this pins that the row itself was never written to.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        role_row = await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()
        ended_at_before = role_row.ended_at
        created_reason_before = role_row.created_reason

        await prune_redundant_direct_access(target=AccessTarget.BOTH, dry_run=False)

        await db.session.refresh(role_row)
        assert role_row.ended_at == ended_at_before
        assert role_row.created_reason == created_reason_before


class TestCliOptions:
    # These assert on the command's declared parameters and its --help output.
    # Click's help option is eager and exits before the callback, so no event
    # loop or database is involved.
    def test_command_is_registered(self) -> None:
        assert "prune-redundant-direct-access" in cli.commands

    def test_help_lists_every_option(self) -> None:
        result = CliRunner().invoke(cli, ["prune-redundant-direct-access", "--help"])

        assert result.exit_code == 0
        for flag in ("--target", "--apply", "--allow-shortening", "--group", "--user", "--app"):
            assert flag in result.output

    def test_defaults_are_dry_run_both_access_types_and_guarded(self) -> None:
        params = {p.name: p for p in prune_redundant_direct_access_command.params}

        assert params["target"].default == "both"
        assert set(params["target"].type.choices) == {"members", "owners", "both"}
        assert params["apply_changes"].default is False
        assert params["allow_shortening"].default is False

    def test_filters_are_repeatable(self) -> None:
        params = {p.name: p for p in prune_redundant_direct_access_command.params}

        assert params["groups"].multiple is True
        assert params["users"].multiple is True
        assert params["apps"].multiple is True


class TestCliCallback:
    # The command body is invoked as `.callback.__wrapped__()`, which peels off
    # `_with_app_context`. CliRunner cannot drive it: CliRunner is synchronous
    # and `_with_app_context` calls asyncio.run(), which would use the test's
    # loop-bound aiosqlite engine from a different loop.
    async def test_dry_run_is_the_default_and_writes_nothing(
        self, db: Db, capsys: pytest.CaptureFixture[str], user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        await prune_redundant_direct_access_command.callback.__wrapped__(
            target="both", apply_changes=False, allow_shortening=False, groups=(), users=(), apps=()
        )

        output = capsys.readouterr().out
        assert "dry run" in output
        assert user.email in output
        assert okta_group.name in output
        assert await _active_direct_count(db, user=user, group=okta_group) == 1

    async def test_apply_removes_and_reports(
        self, db: Db, capsys: pytest.CaptureFixture[str], user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        await prune_redundant_direct_access_command.callback.__wrapped__(
            target="both", apply_changes=True, allow_shortening=False, groups=(), users=(), apps=()
        )

        output = capsys.readouterr().out
        assert "dry run" not in output
        assert "1 removed" in output
        assert await _active_direct_count(db, user=user, group=okta_group) == 0

    async def test_nothing_to_do_reports_cleanly(self, db: Db, capsys: pytest.CaptureFixture[str]) -> None:
        await prune_redundant_direct_access_command.callback.__wrapped__(
            target="both", apply_changes=False, allow_shortening=False, groups=(), users=(), apps=()
        )

        assert "No redundant direct access found" in capsys.readouterr().out

    async def test_unresolvable_filter_raises_a_click_exception(self, db: Db) -> None:
        with pytest.raises(click.ClickException, match="No active group matches 'Nope'"):
            await prune_redundant_direct_access_command.callback.__wrapped__(
                target="both", apply_changes=False, allow_shortening=False, groups=("Nope",), users=(), apps=()
            )

    async def test_a_failed_group_exits_non_zero(
        self, db: Db, mocker: MockerFixture, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # A periodic run that left grants unpruned has to be visible as a failed
        # run, not only as stderr output.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()
        mocker.patch.object(ModifyGroupUsers, "execute", side_effect=RuntimeError("boom"))

        with pytest.raises(SystemExit) as exc_info:
            await prune_redundant_direct_access_command.callback.__wrapped__(
                target="both", apply_changes=True, allow_shortening=False, groups=(), users=(), apps=()
            )

        assert exc_info.value.code == 1

    async def test_a_clean_run_does_not_exit_non_zero(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        await db.session.commit()

        # Must not raise SystemExit: a clean run leaves the exit status at 0.
        await prune_redundant_direct_access_command.callback.__wrapped__(
            target="both", apply_changes=True, allow_shortening=False, groups=(), users=(), apps=()
        )
