import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Define constants for AccessConfig JSON keys
BACKEND = "BACKEND"
NAME_VALIDATION_PATTERN = "NAME_VALIDATION_PATTERN"
NAME_VALIDATION_ERROR = "NAME_VALIDATION_ERROR"


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
    def __init__(self, name_pattern: str, name_validation_error: str):
        self.name_pattern = name_pattern
        self.name_validation_error = name_validation_error


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

    return AccessConfig(
        name_pattern=name_pattern,
        name_validation_error=name_validation_error,
    )


_ACCESS_CONFIG = None


def get_access_config() -> AccessConfig:
    global _ACCESS_CONFIG
    if _ACCESS_CONFIG is None:
        _ACCESS_CONFIG = _load_access_config()
    return _ACCESS_CONFIG


__all__ = ["get_access_config", "AccessConfig"]
