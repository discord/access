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
    ConfigValidationError,
    _validate_override_config,
)


@pytest.fixture
def mock_load_default_config() -> Generator[Any, Any, Any]:
    with patch(
        "api.access_config._load_default_config",
        return_value={
            NAME_VALIDATION_PATTERN: "name_pattern",
            NAME_VALIDATION_ERROR: "name_error",
        },
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
                    },
                },
                config_file,
            )

        config = _load_default_config(temp_dir)
        assert config[NAME_VALIDATION_PATTERN] == "name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "name_error"


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
        }

        _merge_override_config(config, temp_dir)

        assert config[NAME_VALIDATION_PATTERN] == "override_name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "override_name_error"


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

    assert (
        str(exc_info.value)
        == "'NON_EXISTENT_KEY' is not a defined config value in: ['NAME_VALIDATION_ERROR', 'NAME_VALIDATION_PATTERN']"
    )


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
