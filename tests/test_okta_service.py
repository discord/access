from unittest.mock import MagicMock, patch

from api.services.okta_service import is_managed_group


def test_is_managed_group_with_allow_discord_access_false():
    """Test that is_managed_group returns False when allow_discord_access is False."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = False  # Set the profile attribute to False
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is False


def test_is_managed_group_with_allow_discord_access_true():
    """Test that is_managed_group returns True when allow_discord_access is True."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = True  # Set the profile attribute to True
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


def test_is_managed_group_with_allow_discord_access_undefined():
    """Test that is_managed_group returns True when the custom attribute is undefined."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", None):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = False  # Set the profile attribute to False
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True
