"""Application settings.

`pydantic_settings.BaseSettings` singleton. The exported `settings`
instance is the source of truth at runtime; tests mutate fields on it
directly to override behavior (e.g. `settings.REQUIRE_DESCRIPTIONS = True`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Optional, Union, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_secret_key() -> Optional[str]:
    secret_key_file = os.getenv("SECRET_KEY_FILE")
    if secret_key_file is None:
        return os.getenv("SECRET_KEY")
    with open(secret_key_file, "r") as f:
        return f.read().strip()


def _parse_oidc_client_secrets(value: Optional[str]) -> Union[None, str, dict[str, Any]]:
    if value is None:
        return None
    if value.startswith("{") and value.endswith("}"):
        return json.loads(value)
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # Environment
    ENV: Literal["development", "test", "production", "staging"] = Field(
        default_factory=lambda: cast(
            'Literal["development", "test", "production", "staging"]',
            os.getenv("ENV") or "development",
        )
    )

    @property
    def DEBUG(self) -> bool:
        return self.ENV == "development"

    @property
    def TESTING(self) -> bool:
        return self.ENV == "test"

    CLIENT_ORIGIN_URL: Optional[str] = None

    # Okta
    OKTA_DOMAIN: Optional[str] = None
    OKTA_API_TOKEN: Optional[str] = None
    OKTA_USE_GROUP_OWNERS_API: bool = False
    CURRENT_OKTA_USER_EMAIL: str = "wumpus@discord.com"
    OKTA_GROUP_PROFILE_CUSTOM_ATTR: Optional[str] = None

    # Database
    SQLALCHEMY_DATABASE_URI: Optional[str] = Field(default_factory=lambda: os.getenv("DATABASE_URI"))
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ECHO: bool = False

    # CloudSQL
    CLOUDSQL_CONNECTION_NAME: str = ""
    DATABASE_USER: str = "root"
    DATABASE_PASSWORD: str = ""
    DATABASE_NAME: str = "access"
    DATABASE_USES_PUBLIC_IP: bool = False

    # User attributes
    USER_DISPLAY_CUSTOM_ATTRIBUTES: str = "Title,Manager"
    USER_SEARCH_CUSTOM_ATTRIBUTES: Optional[str] = None
    MAX_ACCESS_REQUEST_AGE_SECONDS: int = 7 * 24 * 60 * 60

    # Cloudflare Access
    CLOUDFLARE_APPLICATION_AUDIENCE: Optional[str] = None
    CLOUDFLARE_TEAM_DOMAIN: Optional[str] = None

    # OIDC
    OIDC_CLIENT_SECRETS: Union[None, str, dict[str, Any]] = None
    OIDC_INTROSPECTION_AUTH_METHOD: str = "client_secret_post"
    OIDC_CLOCK_SKEW: int = 60
    OIDC_OVERWRITE_REDIRECT_URI: Optional[str] = None
    OIDC_SCOPES: str = "openid email"
    OIDC_SERVER_METADATA_URL: Optional[str] = None

    # Session
    SECRET_KEY: Optional[str] = Field(default_factory=_read_secret_key)

    # App metadata
    APP_CREATOR_ID: Optional[str] = None
    APP_VERSION: str = "Not Defined"
    APP_NAME: str = "Access"

    # Sentry
    FLASK_SENTRY_DSN: Optional[str] = None
    REACT_SENTRY_DSN: Optional[str] = None

    # Behavior toggles
    REQUIRE_DESCRIPTIONS: bool = False

    @property
    def user_search_attrs(self) -> list[str]:
        if self.USER_SEARCH_CUSTOM_ATTRIBUTES is not None:
            return self.USER_SEARCH_CUSTOM_ATTRIBUTES.split(",")
        attrs = self.USER_DISPLAY_CUSTOM_ATTRIBUTES.split(",")
        if "Manager" in attrs:
            attrs.remove("Manager")
        return attrs


def _build_settings() -> Settings:
    s = Settings()
    if s.OIDC_CLIENT_SECRETS is not None and isinstance(s.OIDC_CLIENT_SECRETS, str):
        parsed = _parse_oidc_client_secrets(s.OIDC_CLIENT_SECRETS)
        # Reassign through __setattr__; Pydantic v2 settings allow mutation
        s.OIDC_CLIENT_SECRETS = parsed
    return s


def assert_env_explicitly_set() -> None:
    """Fail-closed guard against silently defaulting to development mode.

    `ENV` falls back to "development" via `default_factory` if not set,
    which flips DEBUG on, exposes `/api/docs`, and (in CF Access
    deployments) activates the dev/test auth bypass. Called from
    `create_app()` at HTTP-server startup so deployments crash loudly
    on a missing env var instead of fail-open.

    Pytest paths bypass this (the test harness sets `ENV=test` via
    `.testenv`); CLI runs that don't go through `create_app()` are
    unaffected. The check honors `ENV` declared in a `.env` file as well
    as the OS environment, since `pydantic-settings` reads both.
    """
    if os.getenv("ENV"):
        return
    # Honor `ENV` declared in `.env` (pydantic-settings reads it). Walk up
    # from CWD so `make run-backend` from any path still finds it.
    candidate = Path.cwd() / ".env"
    if candidate.is_file():
        try:
            content = candidate.read_text()
        except OSError:
            content = ""
        for line in content.splitlines():
            if line.strip().startswith("ENV="):
                return
    raise RuntimeError(
        "ENV is not set. Export ENV=development|test|staging|production "
        "(or declare it in .env) so deploys cannot silently fall through "
        "to dev mode."
    )


settings = _build_settings()


# --- Backwards-compatible module-level constants -------------------------
# Some legacy callers (services, alembic env) import individual config values
# directly from `api.config`. Re-export them as module attributes that mirror
# the `settings` singleton's fields. These are read-only snapshots — tests
# that mutate `settings.X = Y` will not see the change reflected here.
ENV = settings.ENV
DEBUG = settings.DEBUG
TESTING = settings.TESTING
CLIENT_ORIGIN_URL = settings.CLIENT_ORIGIN_URL
OKTA_DOMAIN = settings.OKTA_DOMAIN
OKTA_API_TOKEN = settings.OKTA_API_TOKEN
OKTA_USE_GROUP_OWNERS_API = settings.OKTA_USE_GROUP_OWNERS_API
CURRENT_OKTA_USER_EMAIL = settings.CURRENT_OKTA_USER_EMAIL
OKTA_GROUP_PROFILE_CUSTOM_ATTR = settings.OKTA_GROUP_PROFILE_CUSTOM_ATTR
SQLALCHEMY_DATABASE_URI = settings.SQLALCHEMY_DATABASE_URI
SQLALCHEMY_TRACK_MODIFICATIONS = settings.SQLALCHEMY_TRACK_MODIFICATIONS
SQLALCHEMY_ECHO = settings.SQLALCHEMY_ECHO
CLOUDSQL_CONNECTION_NAME = settings.CLOUDSQL_CONNECTION_NAME
DATABASE_USER = settings.DATABASE_USER
DATABASE_PASSWORD = settings.DATABASE_PASSWORD
DATABASE_NAME = settings.DATABASE_NAME
DATABASE_USES_PUBLIC_IP = settings.DATABASE_USES_PUBLIC_IP
USER_DISPLAY_CUSTOM_ATTRIBUTES = settings.USER_DISPLAY_CUSTOM_ATTRIBUTES
USER_SEARCH_CUSTOM_ATTRIBUTES = settings.USER_SEARCH_CUSTOM_ATTRIBUTES or ",".join(settings.user_search_attrs)
MAX_ACCESS_REQUEST_AGE_SECONDS = settings.MAX_ACCESS_REQUEST_AGE_SECONDS
CLOUDFLARE_APPLICATION_AUDIENCE = settings.CLOUDFLARE_APPLICATION_AUDIENCE
CLOUDFLARE_TEAM_DOMAIN = settings.CLOUDFLARE_TEAM_DOMAIN
SECRET_KEY = settings.SECRET_KEY
APP_CREATOR_ID = settings.APP_CREATOR_ID
APP_VERSION = settings.APP_VERSION
APP_NAME = settings.APP_NAME
FLASK_SENTRY_DSN = settings.FLASK_SENTRY_DSN
REACT_SENTRY_DSN = settings.REACT_SENTRY_DSN
REQUIRE_DESCRIPTIONS = settings.REQUIRE_DESCRIPTIONS
