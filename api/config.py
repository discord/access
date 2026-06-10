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

    # Comma-separated Host header allowlist for TrustedHostMiddleware
    # (wildcards like "*.example.com" supported). Guards Host-derived URLs such
    # as the OIDC redirect_uri. Empty disables the check; create_app() requires
    # it for OIDC deployments outside development/test.
    ALLOWED_HOSTS: str = ""

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
    # Fixed IdP callback URL. When unset, it is derived from the request Host
    # header (poisonable if the IdP's redirect-URI allowlist is loose).
    # Recommended behind a proxy; complements ALLOWED_HOSTS.
    OIDC_OVERWRITE_REDIRECT_URI: Optional[str] = None
    OIDC_SCOPES: str = "openid email"
    OIDC_SERVER_METADATA_URL: Optional[str] = None
    # Required `aud` claim for MCP-bound OIDC bearer tokens. When the OIDC
    # provider is active for MCP (``OIDC_SERVER_METADATA_URL`` set and
    # ``ENABLE_MCP=true``), every incoming token's ``aud`` claim must match
    # this value or the request is rejected. Required because skipping
    # ``aud`` validation lets a token issued for a different resource
    # server authenticate to Access MCP (the classic confused-deputy /
    # token-confusion attack). Typically the OAuth client identifier for
    # the MCP application registered with the IdP, e.g. ``access-mcp``.
    OIDC_MCP_AUDIENCE: Optional[str] = None

    # Session
    SECRET_KEY: Optional[str] = Field(default_factory=_read_secret_key)

    # App metadata
    # APP_CREATOR_ID accepts a comma-separated list of identifiers permitted
    # to create new apps via POST /api/apps (in addition to Access admins). A
    # single value (no commas) keeps the original single-creator behavior.
    APP_CREATOR_ID: Optional[str] = None
    # APP_GROUP_DELETER_ID accepts a comma-separated list of identifiers
    # permitted to delete managed AppGroups (in addition to the existing
    # group-owner / app-owner / access-admin paths).
    APP_GROUP_DELETER_ID: Optional[str] = None
    APP_VERSION: str = "Not Defined"
    APP_NAME: str = "Access"

    # Sentry
    FASTAPI_SENTRY_DSN: Optional[str] = None
    REACT_SENTRY_DSN: Optional[str] = None

    # Behavior toggles
    REQUIRE_DESCRIPTIONS: bool = False

    # MCP server. Off-by-default; flipping this to True mounts the FastMCP
    # server at /mcp and activates the MCP auth middleware. Most operators
    # of the open-source distribution don't run LLM tooling and shouldn't
    # pay for any of it. See api/mcp/ for the implementation.
    ENABLE_MCP: bool = False

    # Fallback scope set the default Cloudflare auth provider grants when
    # an incoming MCP token carries no `scope` (or `scp`) claim. Comma-
    # separated. CF Managed OAuth does not currently issue tokens with a
    # scope claim, so this fallback fires on every CF-fronted MCP request
    # today.
    #
    # Default is `read_all,create_requests` — every MCP write tool still
    # runs the same authorization predicate (Layer 2) and operation
    # constraints (Layer 3) the matching REST endpoint applies, so this
    # grants the user no capability they don't already have via REST; it
    # just permits the tool to be CALLED. Operators who want a stricter
    # posture can set this to `"read_all"` (read-only sessions) or `""`
    # (fail-closed; only tokens with an explicit scope claim work).
    # When CF (or your provider) starts emitting scope claims, this
    # fallback never fires and the token controls scope per session;
    # the value here becomes inert.
    MCP_FALLBACK_SCOPES: str = "read_all,create_requests"

    # Canonical public URL of the MCP resource, e.g.
    # ``https://access.example.com/mcp``. Surfaced in the RFC 9728 metadata
    # document and the 401 ``resource_metadata`` pointer for client
    # discovery. When unset, derived from the request (forwarded scheme +
    # Host + ``/mcp``); set explicitly behind a proxy that rewrites Host.
    MCP_RESOURCE_URL: Optional[str] = None

    # DNS-rebinding protection for the FastMCP transport layer. Comma-
    # separated Host header allowlist (wildcards like ``"localhost:*"``
    # are supported). Empty (default) disables the check — relies on
    # the auth middleware + CORS + no-browser-client to cover the
    # threat. Set to your public host (``"access.example.com"``) for
    # defense-in-depth, or to ``"localhost:*,127.0.0.1:*"`` for dev
    # with a browser open. Any non-empty value flips protection ON.
    MCP_ALLOWED_HOSTS: str = ""

    @property
    def user_search_attrs(self) -> list[str]:
        if self.USER_SEARCH_CUSTOM_ATTRIBUTES is not None:
            return self.USER_SEARCH_CUSTOM_ATTRIBUTES.split(",")
        attrs = self.USER_DISPLAY_CUSTOM_ATTRIBUTES.split(",")
        if "Manager" in attrs:
            attrs.remove("Manager")
        return attrs

    @property
    def trusted_hosts(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def app_creator_ids(self) -> list[str]:
        if self.APP_CREATOR_ID is None:
            return []
        return [s.strip() for s in self.APP_CREATOR_ID.split(",") if s.strip()]

    @property
    def app_group_deleter_ids(self) -> list[str]:
        if self.APP_GROUP_DELETER_ID is None:
            return []
        return [s.strip() for s in self.APP_GROUP_DELETER_ID.split(",") if s.strip()]


def _build_settings() -> Settings:
    s = Settings()
    if s.OIDC_CLIENT_SECRETS is not None and isinstance(s.OIDC_CLIENT_SECRETS, str):
        parsed = _parse_oidc_client_secrets(s.OIDC_CLIENT_SECRETS)
        # Reassign through __setattr__; Pydantic v2 settings allow mutation
        s.OIDC_CLIENT_SECRETS = parsed
    _validate_mcp_auth_settings(s)
    return s


def _validate_mcp_auth_settings(s: Settings) -> None:
    """Fail-closed guards on the MCP auth configuration.

    Only fires when ``ENABLE_MCP=true``. Two rules:

      1. ``CLOUDFLARE_TEAM_DOMAIN`` and ``OIDC_SERVER_METADATA_URL`` are
         mutually exclusive for MCP. The middleware runs the providers
         in order and the first one to succeed wins, but configuring
         both is almost always a mistake — pick one auth model for the
         MCP surface and stick with it.
      2. The OIDC provider requires ``OIDC_MCP_AUDIENCE``. Skipping
         audience validation lets a token issued for a different
         resource server authenticate to Access MCP, which is the
         classic OAuth confused-deputy attack.
    """
    if not s.ENABLE_MCP:
        return
    cf = bool(s.CLOUDFLARE_TEAM_DOMAIN)
    oidc = bool(s.OIDC_SERVER_METADATA_URL)
    if cf and oidc:
        raise ValueError(
            "ENABLE_MCP=true with both CLOUDFLARE_TEAM_DOMAIN and "
            "OIDC_SERVER_METADATA_URL set: pick one auth model for /mcp"
        )
    if oidc and not s.OIDC_MCP_AUDIENCE:
        raise ValueError(
            "ENABLE_MCP=true with OIDC_SERVER_METADATA_URL set requires "
            "OIDC_MCP_AUDIENCE to validate the token's `aud` claim"
        )


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
FASTAPI_SENTRY_DSN = settings.FASTAPI_SENTRY_DSN
REACT_SENTRY_DSN = settings.REACT_SENTRY_DSN
REQUIRE_DESCRIPTIONS = settings.REQUIRE_DESCRIPTIONS
ENABLE_MCP = settings.ENABLE_MCP
MCP_FALLBACK_SCOPES = settings.MCP_FALLBACK_SCOPES
