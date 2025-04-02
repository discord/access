import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from okta.models.group_rule import GroupRule as OktaGroupRuleType

from api.services.okta_service import OktaService, is_managed_group


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


def test_update_group_preserves_custom_attributes() -> None:
    """Test that update_group preserves custom attributes when updating a group."""
    # Create a new event loop for this test
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Mock asyncio.run to use our event loop
    with patch("asyncio.run", side_effect=lambda x: loop.run_until_complete(x)):
        # Create OktaService instance with mock client
        service = OktaService()
        service.okta_client = MagicMock()

        # Set up the mocks for the existing group and the update call
        group_id = "test-group-id"

        # Create a mock group with a profile that has the custom attribute
        existing_group = MagicMock()
        # Instead of setting __dict__ directly, configure the mock properly
        existing_group.profile = MagicMock()
        existing_group.profile.name = "Old Name"
        existing_group.profile.description = "Old Description"
        existing_group.profile.allow_discord_access = True

        # Mock the get_group and update_group methods
        service.okta_client.get_group = AsyncMock(return_value=(existing_group, None, None))
        service.okta_client.update_group = AsyncMock(return_value=(MagicMock(), None, None))

        # Create a mock for the SessionedOktaRequestExecutor context manager
        # This is a special class that implements the async context manager protocol
        class MockSessionedExecutor:
            """Mock class for SessionedOktaRequestExecutor with async context manager methods"""

            async def __aenter__(self) -> None:
                """Mock for __aenter__ - called when entering an 'async with' block"""
                return None

            async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
                """Mock for __aexit__ - called when exiting an 'async with' block"""
                return None

        # Create a mock context manager instance
        mock_executor = MockSessionedExecutor()

        # Use patch to mock the _get_sessioned_okta_request_executor method
        # This avoids directly assigning to the method, which mypy doesn't like
        with patch.object(service, "_get_sessioned_okta_request_executor", return_value=mock_executor):
            # Call update_group
            service.update_group(group_id, "New Name", "New Description")

            # Verify update_group was called with a payload that preserved the custom attribute
            args, _ = service.okta_client.update_group.call_args
            assert len(args) == 2
            assert args[0] == group_id

            # Check that the payload contains both the updated fields and the preserved custom attribute
            updated_payload = args[1]
            assert updated_payload.profile.name == "New Name"
            assert updated_payload.profile.description == "New Description"
            assert updated_payload.profile.allow_discord_access is True
