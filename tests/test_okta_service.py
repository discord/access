from unittest.mock import Mock

from api.services.okta_service import is_managed_group


def test_is_managed_group_without_allow_discord_access() -> None:
    # Create a mock group with allow_discord_access attribute set to False
    group = Mock()
    group.profile.allow_discord_access = False
    group.type = "OKTA_GROUP"
    group.id = "mock_group_id"

    # Mock group_ids_with_group_rules
    group_ids_with_group_rules: dict[str, list] = {}

    result = is_managed_group(group, group_ids_with_group_rules)

    # Assert the result
    assert result is False


def test_is_managed_group_with_allow_discord_access() -> None:
    # Create a mock group with allow_discord_access attribute set to True
    group = Mock()
    group.profile.allow_discord_access = True
    group.type = "OKTA_GROUP"
    group.id = "mock_group_id"

    group_ids_with_group_rules: dict[str, list] = {}

    result = is_managed_group(group, group_ids_with_group_rules)

    assert result is True


def test_is_managed_group_with_allow_discord_access_undefined() -> None:
    # Create a mock group with allow_discord_access attribute not set
    group = Mock()
    group.profile.allow_discord_access = None
    group.type = "OKTA_GROUP"
    group.id = "mock_group_id"

    group_ids_with_group_rules: dict[str, list] = {}

    result = is_managed_group(group, group_ids_with_group_rules)

    assert result is True
