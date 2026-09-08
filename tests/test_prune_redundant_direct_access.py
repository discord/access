"""Tests for `api.redundant_access` and the `prune-redundant-direct-access` CLI command."""

from datetime import UTC, datetime, timedelta

from api.extensions import Db
from api.models import OktaGroup, OktaUser, OktaUserGroupMember, RoleGroup
from api.redundant_access import AccessTarget, RedundantGrant, _later, find_redundant_grants
from tests.factories import OktaUserGroupMemberFactory, RoleGroupFactory, RoleGroupMapFactory

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
