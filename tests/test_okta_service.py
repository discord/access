from unittest.mock import MagicMock, patch

import pytest

from api.services.okta_service import is_managed_group


# Fixture to set the environment variable for the test
@pytest.fixture
def set_okta_group_profile_custom_attr_env_var():
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        yield


def test_is_managed_group_with_allow_discord_access_false(set_okta_group_profile_custom_attr_env_var):
    # Create a mock of the Group class with all necessary properties
    group = MagicMock()
    group.profile = MagicMock()
    group.profile.allow_discord_access = False  # Set the attribute to False
    group.type = "OKTA_GROUP"
    group.id = "123456789"  # Example group ID

    group_ids_with_group_rules = {}  # Empty dictionary for group rules
    result = is_managed_group(group, group_ids_with_group_rules)

    assert result is False  # Assert that the result is False
