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
    _resolve_config_paths,
    NAME_VALIDATION_PATTERN,
    NAME_VALIDATION_ERROR,
    APP_GROUP_NAME_PREFIX,
    APP_NAME_GROUP_NAME_SEPARATOR,
    ROLE_GROUP_NAME_PREFIX,
    APP_OWNERS_GROUP_NAME_SUFFIX,
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
            APP_GROUP_NAME_PREFIX: "App-",
            APP_NAME_GROUP_NAME_SEPARATOR: "-",
            ROLE_GROUP_NAME_PREFIX: "Role-",
            APP_OWNERS_GROUP_NAME_SUFFIX: "Owners",
        },
    ):
        yield


@pytest.fixture
def mock_merge_override_config() -> Generator[Any, Any, Any]:
    with patch("api.access_config._merge_override_config") as mock_merge:
        mock_merge.side_effect = lambda config, _config_dir, _filename: config.update(
            {
                NAME_VALIDATION_PATTERN: "override_name_pattern",
                NAME_VALIDATION_ERROR: "override_name_error",
                APP_GROUP_NAME_PREFIX: "Override-",
                APP_NAME_GROUP_NAME_SEPARATOR: "_",
                ROLE_GROUP_NAME_PREFIX: "OverrideRole-",
                APP_OWNERS_GROUP_NAME_SUFFIX: "OverrideOwners",
            }
        )
        yield mock_merge


def test_load_config_default(mock_load_default_config: None) -> None:
    config = _load_access_config()
    assert isinstance(config, AccessConfig)
    assert config.name_pattern == "name_pattern"
    assert config.name_validation_error == "name_error"
    assert config.app_group_name_prefix == "App-"
    assert config.app_name_group_name_separator == "-"
    assert config.role_group_name_prefix == "Role-"
    assert config.app_owners_group_name_suffix == "Owners"


def test_load_config_with_override(
    mock_load_default_config: None,
    mock_merge_override_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _load_access_config only calls _merge_override_config when an override
    # is requested; ACCESS_CONFIG_FILE is how a caller asks for one.
    monkeypatch.setenv("ACCESS_CONFIG_FILE", "override.json")
    config = _load_access_config()
    assert isinstance(config, AccessConfig)
    assert config.name_pattern == "override_name_pattern"
    assert config.name_validation_error == "override_name_error"
    assert config.app_group_name_prefix == "Override-"
    assert config.app_name_group_name_separator == "_"
    assert config.role_group_name_prefix == "OverrideRole-"
    assert config.app_owners_group_name_suffix == "OverrideOwners"


def test_load_default_config() -> None:
    with tempfile.TemporaryDirectory() as config_dir:
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

        config = _load_default_config(config_dir)
        assert config[NAME_VALIDATION_PATTERN] == "name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "name_error"


def test_merge_override_config() -> None:
    with tempfile.TemporaryDirectory() as config_dir:
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

        config = {
            NAME_VALIDATION_PATTERN: "group_pattern",
            NAME_VALIDATION_ERROR: "name_error",
        }

        _merge_override_config(config, config_dir, filename)

        assert config[NAME_VALIDATION_PATTERN] == "override_name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "override_name_error"


def test_load_default_config_file_not_found() -> None:
    with pytest.raises(ConfigFileNotFoundError):
        _load_default_config("/non/existent/path")


def test_merge_override_config_ignores_frontend_override() -> None:
    with tempfile.TemporaryDirectory() as config_dir:
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

        config = {
            NAME_VALIDATION_PATTERN: "name_pattern",
            NAME_VALIDATION_ERROR: "name_error",
        }
        _merge_override_config(config, config_dir, filename)
        # no overrides from FRONTEND keys!
        assert config[NAME_VALIDATION_PATTERN] == "name_pattern"
        assert config[NAME_VALIDATION_ERROR] == "name_error"
        # extra key from FRONTEND not there either
        assert "FOO" not in config


def test_resolve_config_paths_bare_filename_uses_bundled_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCESS_CONFIG_FILE", "config.production.json")
    config_dir, override_filename = _resolve_config_paths()
    # Bare filenames keep existing semantics: the bundled config dir is used.
    assert os.path.basename(config_dir) == "config"
    assert override_filename == "config.production.json"


def test_resolve_config_paths_absolute_path_overrides_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCESS_CONFIG_FILE", "/etc/access-config/config.production.json")
    config_dir, override_filename = _resolve_config_paths()
    assert config_dir == "/etc/access-config"
    assert override_filename == "config.production.json"


def test_resolve_config_paths_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCESS_CONFIG_FILE", raising=False)
    config_dir, override_filename = _resolve_config_paths()
    assert os.path.basename(config_dir) == "config"
    assert override_filename is None


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
