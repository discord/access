import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from okta.models.group_rule import GroupRule as OktaGroupRuleType
from okta.request_executor import RequestExecutor

from api.services.okta_service import OktaService, is_managed_group
from tests.factories import UserFactory


def test_is_managed_group_with_allow_discord_access_false() -> None:
    """Test that is_managed_group returns False when allow_discord_access is False."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = False  # Set the profile attribute to False
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is False


def test_is_managed_group_with_allow_discord_access_true() -> None:
    """Test that is_managed_group returns True when allow_discord_access is True."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = True  # Set the profile attribute to True
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


def test_is_managed_group_with_allow_discord_access_undefined() -> None:
    """Test that is_managed_group returns True when the custom attribute is undefined."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", None):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = False  # Set the profile attribute to False
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


async def test_update_group_preserves_custom_attributes() -> None:
    """Test that update_group preserves custom attributes when updating a group."""
    service = OktaService()

    # Set up the mocks for the existing group and the update call
    group_id = "test-group-id"

    # Create a mock group with a profile that has the custom attribute
    existing_group = MagicMock()
    # Instead of setting __dict__ directly, configure the mock properly
    existing_group.profile = MagicMock()
    existing_group.profile.name = "Old Name"
    existing_group.profile.description = "Old Description"
    existing_group.profile.allow_discord_access = True

    # Mock the per-call Okta client's get_group and update_group methods
    mock_client = MagicMock()
    mock_client.get_group = AsyncMock(return_value=(existing_group, None, None))
    mock_client.update_group = AsyncMock(return_value=(MagicMock(), None, None))

    # Mock the _okta_client async context manager to yield the mock client
    class MockOktaClientContext:
        """Async context manager that yields the mock Okta client"""

        async def __aenter__(self) -> MagicMock:
            return mock_client

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

    # Use patch to mock the _okta_client method
    # This avoids directly assigning to the method, which mypy doesn't like
    with patch.object(service, "_okta_client", return_value=MockOktaClientContext()):
        # Call update_group
        await service.update_group(group_id, "New Name", "New Description")

        # Verify update_group was called with a payload that preserved the custom attribute
        args, _ = mock_client.update_group.call_args
        assert len(args) == 2
        assert args[0] == group_id

        # Check that the payload contains both the updated fields and the preserved custom attribute
        updated_payload = args[1]
        assert updated_payload.profile.name == "New Name"
        assert updated_payload.profile.description == "New Description"
        assert updated_payload.profile.allow_discord_access is True


async def test_concurrent_calls_use_isolated_request_executors() -> None:
    """Concurrent Okta calls must each get their own client, executor, and session.

    ``_okta_client()`` builds a fresh SDK client per call. A previous design
    shared one ``self.okta_client`` (and its request executor) across all
    calls, so concurrent ``set_session()`` calls clobbered each other and a
    session created by one call could be torn down under another. With a
    fresh client per call, each call binds its own pooled session to its own
    executor, so concurrent calls on the service share nothing to race on.
    """
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    executors: list[Any] = []

    real_set_session = RequestExecutor.set_session

    def tracking_set_session(executor: Any, session: Any) -> None:
        real_set_session(executor, session)
        executors.append(executor)

    call_count = 16
    success = (UserFactory(), MagicMock(), None)

    with (
        patch.object(RequestExecutor, "set_session", tracking_set_session),
        patch("okta.client.Client.get_user", return_value=success),
    ):
        users = await asyncio.gather(*(service.get_user("okta_id") for _ in range(call_count)))

    # No call raised (gather re-raises), and every call returned a user.
    assert len(users) == call_count
    assert all(user is not None for user in users)
    # Each concurrent call got its own client/request executor; nothing shared.
    assert len(executors) == call_count
    assert len({id(executor) for executor in executors}) == call_count
