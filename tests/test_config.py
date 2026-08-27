import pytest
from pydantic import ValidationError

from api.config import Settings


def test_app_creator_ids() -> None:
    assert Settings(APP_CREATOR_ID=None).app_creator_ids == []
    assert Settings(APP_CREATOR_ID="test1").app_creator_ids == ["test1"]
    assert Settings(APP_CREATOR_ID="test1,test2").app_creator_ids == ["test1", "test2"]


def test_app_group_deleter_ids() -> None:
    assert Settings(APP_GROUP_DELETER_ID=None).app_group_deleter_ids == []
    assert Settings(APP_GROUP_DELETER_ID="test1").app_group_deleter_ids == ["test1"]
    assert Settings(APP_GROUP_DELETER_ID="test1,test2").app_group_deleter_ids == ["test1", "test2"]


def test_expose_api_docs() -> None:
    # Always exposed in development, regardless of the toggle.
    assert Settings(ENV="development", ENABLE_API_DOCS=False).expose_api_docs is True
    # Off by default outside development.
    assert Settings(ENV="staging").expose_api_docs is False
    assert Settings(ENV="production").expose_api_docs is False
    # The toggle opts staging/production in.
    assert Settings(ENV="staging", ENABLE_API_DOCS=True).expose_api_docs is True
    assert Settings(ENV="production", ENABLE_API_DOCS=True).expose_api_docs is True


def test_trusted_hosts() -> None:
    assert Settings(ALLOWED_HOSTS="").trusted_hosts == []
    assert Settings(ALLOWED_HOSTS="access.example.com").trusted_hosts == ["access.example.com"]
    # Comma-separated, surrounding whitespace stripped, blanks dropped.
    assert Settings(ALLOWED_HOSTS=" a.example.com , *.example.com ,").trusted_hosts == [
        "a.example.com",
        "*.example.com",
    ]


def test_request_age_defaults_are_one_week_and_independent() -> None:
    one_week = 7 * 24 * 60 * 60
    assert Settings().max_access_request_age_seconds == one_week
    assert Settings().max_group_request_age_seconds == one_week
    # Independent: setting one does not move the other.
    s = Settings(MAX_ACCESS_REQUEST_AGE_SECONDS=1234)
    assert s.max_access_request_age_seconds == 1234
    assert s.max_group_request_age_seconds == one_week
    s = Settings(MAX_GROUP_REQUEST_AGE_SECONDS=9999)
    assert s.max_access_request_age_seconds == one_week
    assert s.max_group_request_age_seconds == 9999


def test_never_disables_each_age_cutoff_independently() -> None:
    one_week = 7 * 24 * 60 * 60
    s = Settings(MAX_ACCESS_REQUEST_AGE_SECONDS="never")
    assert s.max_access_request_age_seconds is None
    assert s.max_group_request_age_seconds == one_week

    s = Settings(MAX_GROUP_REQUEST_AGE_SECONDS="never")
    assert s.max_access_request_age_seconds == one_week
    assert s.max_group_request_age_seconds is None

    s = Settings(MAX_ACCESS_REQUEST_AGE_SECONDS="never", MAX_GROUP_REQUEST_AGE_SECONDS="never")
    assert s.max_access_request_age_seconds is None
    assert s.max_group_request_age_seconds is None


@pytest.mark.parametrize("bad", ["NEVER", "Never", "nope", "", "none", "null"])
def test_non_numeric_values_other_than_never_are_rejected(bad: str) -> None:
    """A typo must fail at startup rather than silently disabling or defaulting."""
    with pytest.raises(ValidationError):
        Settings(MAX_ACCESS_REQUEST_AGE_SECONDS=bad)
    with pytest.raises(ValidationError):
        Settings(MAX_GROUP_REQUEST_AGE_SECONDS=bad)


@pytest.mark.parametrize("bad", [0, -1, -604800])
def test_ints_below_one_are_rejected_and_the_error_names_never(bad: int) -> None:
    """`-1` is the likely Unix-habit guess for "disable"; expiring everything
    because of it would be a bad silent failure."""
    for field in ("MAX_ACCESS_REQUEST_AGE_SECONDS", "MAX_GROUP_REQUEST_AGE_SECONDS"):
        with pytest.raises(ValidationError) as exc:
            Settings(**{field: bad})
        assert "never" in str(exc.value)
