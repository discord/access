import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Define constants for AccessConfig JSON keys
BACKEND = "BACKEND"
NAME_VALIDATION_PATTERN = "NAME_VALIDATION_PATTERN"
NAME_VALIDATION_ERROR = "NAME_VALIDATION_ERROR"
DEFAULT_GROUP_QUERY_PARAMS = "DEFAULT_GROUP_QUERY_PARAMS"
OKTA_GROUP_NAME_PREFIX = "OKTA_GROUP_NAME_PREFIX"
ROLE_GROUP_NAME_PREFIX = "ROLE_GROUP_NAME_PREFIX"
APP_GROUP_NAME_PREFIX = "APP_GROUP_NAME_PREFIX"
APP_NAME_GROUP_NAME_SEPARATOR = "APP_NAME_GROUP_NAME_SEPARATOR"
APP_OWNERS_GROUP_NAME_SUFFIX = "APP_OWNERS_GROUP_NAME_SUFFIX"


class UndefinedConfigKeyError(Exception):
    def __init__(self, key: str, config: dict[str, Any]):
        super().__init__(f"'{key}' is not a defined config value in: {sorted(config.keys())}")


class ConfigFileNotFoundError(Exception):
    def __init__(self, file_path: str):
        super().__init__(f"Config override file not found: {file_path}")


class ConfigValidationError(Exception):
    def __init__(self, error: str):
        super().__init__(f"Config validation failed: {error}")


class AccessConfig:
    def __init__(
        self,
        name_pattern: str,
        name_validation_error: str,
        default_group_query_params: dict[str, str],
        okta_group_name_prefix: str,
        role_group_name_prefix: str,
        app_group_name_prefix: str,
        app_name_group_name_separator: str,
        app_owners_group_name_suffix: str,
    ):
        self.name_pattern = name_pattern
        self.name_validation_error = name_validation_error
        self.default_group_query_params = default_group_query_params
        self.okta_group_name_prefix = okta_group_name_prefix
        self.role_group_name_prefix = role_group_name_prefix
        self.app_group_name_prefix = app_group_name_prefix
        self.app_name_group_name_separator = app_name_group_name_separator
        self.app_owners_group_name_suffix = app_owners_group_name_suffix


def _get_config_value(config: dict[str, Any], key: str) -> Any:
    if key in config:
        return config[key]
    else:
        raise UndefinedConfigKeyError(key, config)


def _validate_override_config(config: dict[str, Any]) -> None:
    if (NAME_VALIDATION_PATTERN in config) != (NAME_VALIDATION_ERROR in config):
        raise ConfigValidationError(
            f"If either {NAME_VALIDATION_PATTERN} or {NAME_VALIDATION_ERROR} is present, the other must also be present."
        )


def _merge_override_config(config: dict[str, Any], top_level_dir: str) -> None:
    access_config_file = os.getenv("ACCESS_CONFIG_FILE")
    if access_config_file:
        override_config_path = os.path.join(top_level_dir, "config", access_config_file)
        if os.path.exists(override_config_path):
            logger.debug(f"Loading access config override from {override_config_path}")
            with open(override_config_path, "r") as f:
                override_config = json.load(f).get(BACKEND, {})
                _validate_override_config(override_config)
                config.update(override_config)
        else:
            raise ConfigFileNotFoundError(str(override_config_path))


def _load_default_config(top_level_dir: str) -> dict[str, Any]:
    default_config_path = os.path.join(top_level_dir, "config", "config.default.json")
    if not os.path.exists(default_config_path):
        raise ConfigFileNotFoundError(str(default_config_path))
    with open(default_config_path, "r") as f:
        config = json.load(f).get(BACKEND, {})
    return config


def _load_access_config() -> AccessConfig:
    top_level_dir = os.path.dirname(os.path.dirname(__file__))
    config = _load_default_config(top_level_dir)
    _merge_override_config(config, top_level_dir)

    name_pattern = _get_config_value(config, NAME_VALIDATION_PATTERN)
    name_validation_error = _get_config_value(config, NAME_VALIDATION_ERROR)
    default_group_query_params = config.get(
        DEFAULT_GROUP_QUERY_PARAMS, {"filter": 'type eq "BUILT_IN" or type eq "OKTA_GROUP"'}
    )
    okta_group_name_prefix = _get_config_value(config, OKTA_GROUP_NAME_PREFIX)
    role_group_name_prefix = _get_config_value(config, ROLE_GROUP_NAME_PREFIX)
    app_group_name_prefix = _get_config_value(config, APP_GROUP_NAME_PREFIX)
    app_name_group_name_separator = _get_config_value(config, APP_NAME_GROUP_NAME_SEPARATOR)
    app_owners_group_name_suffix = _get_config_value(config, APP_OWNERS_GROUP_NAME_SUFFIX)

    return AccessConfig(
        name_pattern=name_pattern,
        name_validation_error=name_validation_error,
        default_group_query_params=default_group_query_params,
        okta_group_name_prefix=okta_group_name_prefix,
        role_group_name_prefix=role_group_name_prefix,
        app_group_name_prefix=app_group_name_prefix,
        app_name_group_name_separator=app_name_group_name_separator,
        app_owners_group_name_suffix=app_owners_group_name_suffix,
    )


_ACCESS_CONFIG = None


def get_access_config() -> AccessConfig:
    global _ACCESS_CONFIG
    if _ACCESS_CONFIG is None:
        _ACCESS_CONFIG = _load_access_config()
    return _ACCESS_CONFIG


__all__ = ["get_access_config", "AccessConfig"]
