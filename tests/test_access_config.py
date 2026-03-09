import json
import os
import tempfile
from typing import Generator, Any
from unittest.mock import patch

import pytest

from api.access_config import (
    _load_access_config,
    AccessConfig,
    ConfigFileNotFoundError,
    UndefinedConfigKeyError,
    _load_default_config,
    _get_config_value,
    BACKEND,
    _merge_override_config,
    NAME_VALIDATION_PATTERN,
    NAME_VALIDATION_ERROR,
    OKTA_GROUP_NAME_PREFIX,
    ROLE_GROUP_NAME_PREFIX,
    APP_GROUP_NAME_PREFIX,
    APP_NAME_GROUP_NAME_SEPARATOR,
    APP_OWNERS_GROUP_NAME_SUFFIX,
    ConfigValidationError,
    _validate_override_config,
)


DEFAULT_CONFIG_VALUES = {
    NAME_VALIDATION_PATTERN: "name_pattern",
    NAME_VALIDATION_ERROR: "name_error",
    OKTA_GROUP_NAME_PREFIX: "",
    ROLE_GROUP_NAME_PREFIX: "Role-",
    APP_GROUP_NAME_PREFIX: "App-",
    APP_NAME_GROUP_NAME_SEPARATOR: "-",
    APP_OWNERS_GROUP_NAME_SUFFIX: "Owners",
}


@pytest.fixture
def mock_load_default_config() -> Generator[Any, Any, Any]:
    with patch(
        "api.access_config._load_default_config",
        return_value=DEFAULT_CONFIG_VALUES.copy(),
    ):
        yield


@pytest.fixture
def mock_merge_override_config() -> Generator[Any, Any, Any]:
    with patch("api.access_config._merge_override_config") as mock_merge:
        mock_merge.side_effect = lambda config, _: config.update(
            {
                NAME_VALIDATION_PATTERN: "override_name_pattern",
                NAME_VALIDATION_ERROR: "override_name_error",
            }
        )
        yield mock_merge


def test_load_config_default(mock_load_default_config: None) -> None:
    config = _load_access_config()
    assert isinstance(config, AccessConfig)
    assert config.name_pattern == "name_pattern"
    assert config.name_validation_error == "name_error"
    assert config.okta_group_name_prefix == ""
    assert config.role_group_name_prefix == "Role-"
    assert config.app_group_name_prefix == "App-"
    assert config.app_name_group_name_separator == "-"
    assert config.app_owners_group_name_suffix == "Owners"


def test_load_config_with_override(mock_load_default_config: None, mock_merge_override_config: None) -> None:
    config = _load_access_config()
    assert isinstance(config, AccessConfig)
    assert config.name_pattern == "override_name_pattern"
    assert config.name_validation_error == "override_name_error"


def test_load_default_config() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = os.path.join(temp_dir, "config")
        os.makedirs(config_dir)

        config_file_path = os.path.join(config_dir, "config.default.json")
        with open(config_file_path, "w") as config_file:
            json.dump(
                {
                    BACKEND: {
                        NAME_VALIDATION_PATTERN: "name_pattern",
                        NAME_VALIDATION_ERROR: "name_error",
                        OKTA_GROUP_NAME_PREFIX: "",
                        ROLE_GROUP_NAME_PREFIX: "Role-",
                        APP_GROUP_NAME_PREFIX: "App-",
                        APP_NAME_GROUP_NAME_SEPARATOR: "-",
                        APP_OWNERS_GROUP_NAME_SUFFIX: "Owners",
                    },
                },
                config_file,
            )

        config = _load_default_config(temp_dir)
        assert config[NAME_VALIDATION_PATTERN] == "name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "name_error"
        assert config[OKTA_GROUP_NAME_PREFIX] == ""
        assert config[ROLE_GROUP_NAME_PREFIX] == "Role-"
        assert config[APP_GROUP_NAME_PREFIX] == "App-"
        assert config[APP_NAME_GROUP_NAME_SEPARATOR] == "-"
        assert config[APP_OWNERS_GROUP_NAME_SUFFIX] == "Owners"


def test_merge_override_config(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = os.path.join(temp_dir, "config")
        os.makedirs(config_dir)

        filename = "override_config1.json"
        override_config_path = os.path.join(config_dir, filename)
        override_config = {
            BACKEND: {
                NAME_VALIDATION_PATTERN: "override_name_pattern",
                NAME_VALIDATION_ERROR: "override_name_error",
            }
        }
        with open(override_config_path, "w") as config_file:
            json.dump(override_config, config_file)

        # Mock the ACCESS_CONFIG_FILE environment variable
        monkeypatch.setenv("ACCESS_CONFIG_FILE", filename)

        config = {
            NAME_VALIDATION_PATTERN: "group_pattern",
            NAME_VALIDATION_ERROR: "name_error",
            OKTA_GROUP_NAME_PREFIX: "",
            ROLE_GROUP_NAME_PREFIX: "Role-",
            APP_GROUP_NAME_PREFIX: "App-",
            APP_NAME_GROUP_NAME_SEPARATOR: "-",
            APP_OWNERS_GROUP_NAME_SUFFIX: "Owners",
        }

        _merge_override_config(config, temp_dir)

        assert config[NAME_VALIDATION_PATTERN] == "override_name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "override_name_error"
        # prefix keys are preserved when not in override
        assert config[ROLE_GROUP_NAME_PREFIX] == "Role-"
        assert config[APP_GROUP_NAME_PREFIX] == "App-"


def test_load_default_config_file_not_found() -> None:
    with pytest.raises(ConfigFileNotFoundError):
        _load_default_config("/non/existent/path")


def test_merge_override_config_ignores_frontend_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = os.path.join(temp_dir, "config")
        os.makedirs(config_dir)

        filename = "override_config2.json"
        override_config_path = os.path.join(config_dir, filename)
        override_config = {
            "FRONTEND": {
                # even if the key in FRONTEND has the same name as a key in BACKEND, it should not override the back-end value
                NAME_VALIDATION_PATTERN: "override_name_pattern",
                NAME_VALIDATION_ERROR: "override_name_error",
                "FOO": "bar",
            }
        }
        with open(override_config_path, "w") as config_file:
            json.dump(override_config, config_file)

        # Mock the ACCESS_CONFIG_FILE environment variable
        monkeypatch.setenv("ACCESS_CONFIG_FILE", filename)

        config = {
            NAME_VALIDATION_PATTERN: "name_pattern",
            NAME_VALIDATION_ERROR: "name_error",
            OKTA_GROUP_NAME_PREFIX: "",
            ROLE_GROUP_NAME_PREFIX: "Role-",
            APP_GROUP_NAME_PREFIX: "App-",
            APP_NAME_GROUP_NAME_SEPARATOR: "-",
            APP_OWNERS_GROUP_NAME_SUFFIX: "Owners",
        }
        _merge_override_config(config, temp_dir)
        # no overrides from FRONTEND keys!
        assert config[NAME_VALIDATION_PATTERN] == "name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "name_error"
        # extra key from FRONTEND not there either
        assert "FOO" not in config


def test_get_config_value_raises_undefined_config_key_error() -> None:
    config = {
        NAME_VALIDATION_PATTERN: "name_pattern",
        NAME_VALIDATION_ERROR: "name_error",
    }

    with pytest.raises(UndefinedConfigKeyError) as exc_info:
        _get_config_value(config, "NON_EXISTENT_KEY")

    assert "NON_EXISTENT_KEY" in str(exc_info.value)
    assert "is not a defined config value" in str(exc_info.value)


def test_load_config_with_prefix_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = os.path.join(temp_dir, "config")
        os.makedirs(config_dir)

        filename = "override_prefixes.json"
        override_config_path = os.path.join(config_dir, filename)
        override_config = {
            BACKEND: {
                ROLE_GROUP_NAME_PREFIX: "MyRole-",
                APP_GROUP_NAME_PREFIX: "MyApp-",
                APP_NAME_GROUP_NAME_SEPARATOR: "_",
                APP_OWNERS_GROUP_NAME_SUFFIX: "Admin",
                OKTA_GROUP_NAME_PREFIX: "Grp-",
            }
        }
        with open(override_config_path, "w") as config_file:
            json.dump(override_config, config_file)

        monkeypatch.setenv("ACCESS_CONFIG_FILE", override_config_path)

        with patch(
            "api.access_config._load_default_config",
            return_value=DEFAULT_CONFIG_VALUES.copy(),
        ):
            config = _load_access_config()

        assert config.role_group_name_prefix == "MyRole-"
        assert config.app_group_name_prefix == "MyApp-"
        assert config.app_name_group_name_separator == "_"
        assert config.app_owners_group_name_suffix == "Admin"
        assert config.okta_group_name_prefix == "Grp-"
        # unrelated keys keep their defaults
        assert config.name_pattern == DEFAULT_CONFIG_VALUES[NAME_VALIDATION_PATTERN]
        assert config.name_validation_error == DEFAULT_CONFIG_VALUES[NAME_VALIDATION_ERROR]


def test_validate_override_config_raises_error_on_partial_override() -> None:
    expected_error_msg = f"Config validation failed: If either {NAME_VALIDATION_PATTERN} or {NAME_VALIDATION_ERROR} is present, the other must also be present."
    with pytest.raises(ConfigValidationError) as exc_info:
        _validate_override_config(
            {
                NAME_VALIDATION_PATTERN: "override_name_pattern",
            }
        )
    assert str(exc_info.value) == expected_error_msg

    with pytest.raises(ConfigValidationError) as exc_info:
        _validate_override_config(
            {
                NAME_VALIDATION_ERROR: "override_name_error",
            }
        )
    assert str(exc_info.value) == expected_error_msg
