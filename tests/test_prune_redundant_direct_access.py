"""Tests for `api.redundant_access` and the `prune-redundant-direct-access` CLI command."""

from datetime import UTC, datetime, timedelta

import pytest

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.redundant_access import (
    AccessTarget,
    FilterResolutionError,
    RedundantGrant,
    _later,
    find_redundant_grants,
    resolve_group_ids,
    resolve_user_ids,
)
from tests.factories import (
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserFactory,
    OktaUserGroupMemberFactory,
    RoleGroupFactory,
    RoleGroupMapFactory,
)

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

    def test_both_covers_both_dimensions(self) -> None:
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
        # dimensions grant different things.
        db.session.add_all([user, okta_group, role_group])
        await db.session.commit()
        await _direct_grant(user=user, group=okta_group, is_owner=False)
        await _role_grant(user=user, group=okta_group, role_group=role_group, is_owner=True)
        await db.session.commit()

        assert await find_redundant_grants(target=AccessTarget.BOTH) == []

    async def test_target_narrows_to_one_dimension(
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

    async def test_cross_product_of_two_users_and_two_groups_excludes_non_candidates(
        self, db: Db, user: OktaUser, okta_group: OktaGroup, role_group: RoleGroup
    ) -> None:
        # The second query narrows on `user_id.in_(...)` AND `group_id.in_(...)`
        # independently, so with two candidates in play it can also match
        # (user_a, group_b) and (user_b, group_a) role-derived rows even though
        # neither pairing is an actual candidate. `user_a` has a direct grant
        # plus role coverage in `group_a`, and separately only role coverage
        # (no direct grant) in `group_b` -- the cross-product row that would
        # surface. `user_b` is the mirror: a direct grant plus role coverage in
        # `group_b`, so the pair of users and pair of groups both appear in the
        # narrowing sets and the cross product actually forms.
        #
        # This pins down that cross-product rows do not corrupt the result. It
        # does not pin down the `key not in direct_latest` guard itself, which
        # cannot change the output while the results loop reads `role_latest`
        # only at keys drawn from `direct_latest`; it would begin to if that
        # loop ever iterated `role_latest` directly.
        user_b = OktaUserFactory.build()
        group_b = OktaGroupFactory.build()
        db.session.add_all([user, user_b, okta_group, group_b, role_group])
        await db.session.commit()

        # Genuine candidate: (user, okta_group, member).
        await _direct_grant(user=user, group=okta_group)
        await _role_grant(user=user, group=okta_group, role_group=role_group)
        # Role-only coverage for `user` in `group_b`: not a candidate, but pulls
        # `group_b` into the cross product alongside `user`.
        await _role_grant(user=user, group=group_b, role_group=role_group, ended_at=IN_30_DAYS)

        # Genuine candidate: (user_b, group_b, member).
        await _direct_grant(user=user_b, group=group_b)
        await _role_grant(user=user_b, group=group_b, role_group=role_group)
        # Role-only coverage for `user_b` in `okta_group`: not a candidate, but
        # pulls `user_b` into the cross product alongside `okta_group`.
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
        user_with_underscore = OktaUserFactory.build(email="jane_doe@example.com")
        user_without_underscore = OktaUserFactory.build(email="janexdoe@example.com")
        db.session.add_all([user_with_underscore, user_without_underscore])
        await db.session.commit()

        # Querying with the underscore email matches both users via ILIKE.
        # scalar() returns the first row the DB returns, so the result is one of
        # the two ids; the specific one is database-dependent.
        result_ids = await resolve_user_ids(("jane_doe@example.com",))

        assert result_ids == {user_with_underscore.id} or result_ids == {user_without_underscore.id}
        assert len(result_ids) == 1
        assert result_ids <= {user_with_underscore.id, user_without_underscore.id}
