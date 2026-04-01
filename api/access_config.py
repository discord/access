import json
import logging
import os
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Define constants for AccessConfig JSON keys
BACKEND = "BACKEND"
NAME_VALIDATION_PATTERN = "NAME_VALIDATION_PATTERN"
NAME_VALIDATION_ERROR = "NAME_VALIDATION_ERROR"
APP_GROUP_NAME_PREFIX = "APP_GROUP_NAME_PREFIX"
APP_NAME_GROUP_NAME_SEPARATOR = "APP_NAME_GROUP_NAME_SEPARATOR"
ROLE_GROUP_NAME_PREFIX = "ROLE_GROUP_NAME_PREFIX"


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
        app_group_name_prefix: str,
        app_name_group_name_separator: str,
        role_group_name_prefix: str,
    ):
        self.name_pattern = name_pattern
        self.name_validation_error = name_validation_error
        self.app_group_name_prefix = app_group_name_prefix
        self.app_name_group_name_separator = app_name_group_name_separator
        self.role_group_name_prefix = role_group_name_prefix


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


def _merge_override_config(config: dict[str, Any], config_dir: str, override_filename: str) -> None:
    override_config_path = os.path.join(config_dir, override_filename)
    if os.path.exists(override_config_path):
        logger.debug(f"Loading access config override from {override_config_path}")
        with open(override_config_path, "r") as f:
            override_config = json.load(f).get(BACKEND, {})
            _validate_override_config(override_config)
            config.update(override_config)
    else:
        raise ConfigFileNotFoundError(str(override_config_path))


def _load_default_config(config_dir: str) -> dict[str, Any]:
    default_config_path = os.path.join(config_dir, "config.default.json")
    if not os.path.exists(default_config_path):
        raise ConfigFileNotFoundError(str(default_config_path))
    with open(default_config_path, "r") as f:
        config = json.load(f).get(BACKEND, {})
    return config


def _resolve_config_paths() -> Tuple[str, Optional[str]]:
    """Resolve the directory holding config.default.json and an optional override filename.

    ACCESS_CONFIG_FILE accepts either of two shapes:
      - bare filename (e.g. "config.production.json"): existing behavior --
        looked up inside the package's bundled `config/` dir.
      - absolute path (e.g. "/etc/access/config.production.json"): the
        containing directory becomes the lookup dir for BOTH the default
        and the override, so operators can mount a ConfigMap (or any
        other directory) without rebuilding the image.
    """
    bundled_config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    access_config_file = os.getenv("ACCESS_CONFIG_FILE")
    if access_config_file and os.path.isabs(access_config_file):
        return os.path.dirname(access_config_file), os.path.basename(access_config_file)
    return bundled_config_dir, access_config_file


def _load_access_config() -> AccessConfig:
    config_dir, override_filename = _resolve_config_paths()
    config = _load_default_config(config_dir)
    if override_filename:
        _merge_override_config(config, config_dir, override_filename)

    name_pattern = _get_config_value(config, NAME_VALIDATION_PATTERN)
    name_validation_error = _get_config_value(config, NAME_VALIDATION_ERROR)
    app_group_name_prefix = _get_config_value(config, APP_GROUP_NAME_PREFIX)
    app_name_group_name_separator = _get_config_value(config, APP_NAME_GROUP_NAME_SEPARATOR)
    role_group_name_prefix = _get_config_value(config, ROLE_GROUP_NAME_PREFIX)

    return AccessConfig(
        name_pattern=name_pattern,
        name_validation_error=name_validation_error,
        app_group_name_prefix=app_group_name_prefix,
        app_name_group_name_separator=app_name_group_name_separator,
        role_group_name_prefix=role_group_name_prefix,
    )


_ACCESS_CONFIG = None


def get_access_config() -> AccessConfig:
    global _ACCESS_CONFIG
    if _ACCESS_CONFIG is None:
        _ACCESS_CONFIG = _load_access_config()
    return _ACCESS_CONFIG


__all__ = ["get_access_config", "AccessConfig"]
