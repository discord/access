from typing import Dict, Generator, List
from unittest.mock import Mock, patch

import pytest
from okta.models.group_rule import GroupRule as OktaGroupRuleType

from api.services.okta_service import is_managed_group


@pytest.fixture
def okta_group_profile_custom_attr() -> Generator[None, None, None]:
    with patch.dict("os.environ", {"OKTA_GROUP_PROFILE_CUSTOM_ATTR": "allow_discord_access"}):
        yield


def test_is_managed_group_without_allow_discord_access(okta_group_profile_custom_attr: None) -> None:
    group: Mock = Mock()
    group.profile = Mock()
    setattr(group.profile, "allow_discord_access", False)
    group.type = "OKTA_GROUP"
    group.id = "mock_group_id"

    group_ids_with_group_rules: Dict[str, List[OktaGroupRuleType]] = {}

    result = is_managed_group(group, group_ids_with_group_rules)

    assert result is False


def test_is_managed_group_with_allow_discord_access(okta_group_profile_custom_attr: None) -> None:
    group: Mock = Mock()
    group.profile = Mock()
    setattr(group.profile, "allow_discord_access", True)
    group.type = "OKTA_GROUP"
    group.id = "mock_group_id"

    group_ids_with_group_rules: Dict[str, List[OktaGroupRuleType]] = {"mock_group_id": [Mock(spec=OktaGroupRuleType)]}

    result = is_managed_group(group, group_ids_with_group_rules)

    assert result is True


def test_is_managed_group_with_allow_discord_access_undefined(okta_group_profile_custom_attr: None) -> None:
    group: Mock = Mock()
    group.profile = Mock()
    setattr(group.profile, "allow_discord_access", None)
    group.type = "OKTA_GROUP"
    group.id = "mock_group_id"

    group_ids_with_group_rules: Dict[str, List[OktaGroupRuleType]] = {}

    result = is_managed_group(group, group_ids_with_group_rules)

    assert result is True
