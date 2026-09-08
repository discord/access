"""Tests for `api.redundant_access` and the `prune-redundant-direct-access` CLI command."""

from datetime import UTC, datetime, timedelta

from api.redundant_access import AccessTarget, RedundantGrant, _later

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
