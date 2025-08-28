"""
FastAPI configuration using pydantic-settings.
Loads settings from environment variables and .env file.
"""

import json
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment and Debug
    env: str = Field(default="production", alias="ENV")
    debug: bool = Field(default=False)
    app_version: str = Field(default="Not Defined", alias="APP_VERSION")

    # Client Origin
    client_origin_url: Optional[str] = Field(default=None, alias="CLIENT_ORIGIN_URL")

    # Database Configuration
    database_uri: Optional[str] = Field(default=None, alias="DATABASE_URI")
    database_user: str = Field(default="root", alias="DATABASE_USER")
    database_password: str = Field(default="", alias="DATABASE_PASSWORD")
    database_name: str = Field(default="access", alias="DATABASE_NAME")
    database_uses_public_ip: bool = Field(default=False, alias="DATABASE_USES_PUBLIC_IP")
    cloudsql_connection_name: str = Field(default="", alias="CLOUDSQL_CONNECTION_NAME")
    sqlalchemy_track_modifications: bool = Field(default=False, alias="SQLALCHEMY_TRACK_MODIFICATIONS")
    sqlalchemy_echo: bool = Field(default=False, alias="SQLALCHEMY_ECHO")

    # Okta Configuration
    okta_domain: Optional[str] = Field(default=None, alias="OKTA_DOMAIN")
    okta_api_token: Optional[str] = Field(default=None, alias="OKTA_API_TOKEN")
    okta_use_group_owners_api: bool = Field(default=False, alias="OKTA_USE_GROUP_OWNERS_API")
    okta_group_profile_custom_attr: Optional[str] = Field(default=None, alias="OKTA_GROUP_PROFILE_CUSTOM_ATTR")
    okta_webhook_id: Optional[str] = Field(default=None, alias="OKTA_WEBHOOK_ID")
    okta_iga_actor_id: Optional[str] = Field(default=None, alias="OKTA_IGA_ACTOR_ID")

    # User Configuration
    current_okta_user_email: str = Field(default="wumpus@discord.com", alias="CURRENT_OKTA_USER_EMAIL")
    user_display_custom_attributes: str = Field(default="Title,Manager", alias="USER_DISPLAY_CUSTOM_ATTRIBUTES")
    user_search_custom_attributes: Optional[str] = Field(default=None, alias="USER_SEARCH_CUSTOM_ATTRIBUTES")
    max_access_request_age_seconds: int = Field(default=7 * 24 * 60 * 60, alias="MAX_ACCESS_REQUEST_AGE_SECONDS")

    # Cloudflare Access Configuration
    cloudflare_application_audience: Optional[str] = Field(default=None, alias="CLOUDFLARE_APPLICATION_AUDIENCE")
    cloudflare_team_domain: Optional[str] = Field(default=None, alias="CLOUDFLARE_TEAM_DOMAIN")

    # OIDC Configuration
    oidc_client_secrets: Optional[str] = Field(default=None, alias="OIDC_CLIENT_SECRETS")
    oidc_introspection_auth_method: str = Field(default="client_secret_post", alias="OIDC_INTROSPECTION_AUTH_METHOD")
    oidc_clock_skew: int = Field(default=60, alias="OIDC_CLOCK_SKEW")
    oidc_overwrite_redirect_uri: Optional[str] = Field(default=None, alias="OIDC_OVERWRITE_REDIRECT_URI")
    oidc_scopes: str = Field(default="openid email", alias="OIDC_SCOPES")
    oidc_server_metadata_url: Optional[str] = Field(default=None, alias="OIDC_SERVER_METADATA_URL")

    # Secret Key Configuration
    secret_key: Optional[str] = Field(default=None, alias="SECRET_KEY")
    secret_key_file: Optional[str] = Field(default=None, alias="SECRET_KEY_FILE")

    # App Creator
    app_creator_id: Optional[str] = Field(default=None, alias="APP_CREATOR_ID")

    # Sentry Configuration
    flask_sentry_dsn: Optional[str] = Field(default=None, alias="FLASK_SENTRY_DSN")
    react_sentry_dsn: Optional[str] = Field(default=None, alias="REACT_SENTRY_DSN")

    @field_validator("debug", mode="before")
    @classmethod
    def set_debug(cls, v: Any) -> bool:
        """Set debug based on environment."""
        if isinstance(v, bool):
            return v
        # Default to False, will be set based on env
        return False

    @field_validator("sqlalchemy_echo", mode="before")
    @classmethod
    def set_sqlalchemy_echo(cls, v: Any) -> bool:
        """Set sqlalchemy_echo based on environment."""
        if isinstance(v, bool):
            return v
        # Default to False, will be set based on env
        return False

    def __init__(self, **values):
        """Initialize settings and apply post-processing."""
        super().__init__(**values)

        # Set debug based on environment if not explicitly set
        if not isinstance(values.get("debug"), bool):
            self.debug = self.env == "development"

        # Set sqlalchemy_echo based on environment if not explicitly set
        if not isinstance(values.get("sqlalchemy_echo"), bool):
            self.sqlalchemy_echo = self.env == "development"

        # Set user_search_custom_attributes if not provided
        if not self.user_search_custom_attributes:
            attrs = self.user_display_custom_attributes.split(",")
            if "Manager" in attrs:
                attrs.remove("Manager")
            self.user_search_custom_attributes = ",".join(attrs)

        # Load secret key from file if needed
        if not self.secret_key and self.secret_key_file:
            try:
                with open(self.secret_key_file, "r") as f:
                    self.secret_key = f.read().strip()
            except FileNotFoundError:
                raise ValueError(f"Secret key file not found: {self.secret_key_file}")

    def get_oidc_client_secrets_dict(self) -> Optional[Dict[str, Any]]:
        """Parse OIDC client secrets as dictionary if it's a JSON string."""
        if not self.oidc_client_secrets:
            return None

        if self.oidc_client_secrets.startswith("{") and self.oidc_client_secrets.endswith("}"):
            try:
                return json.loads(self.oidc_client_secrets)
            except json.JSONDecodeError:
                return None

        return None

    def get_user_display_attributes_list(self) -> List[str]:
        """Get user display attributes as a list."""
        return [attr.strip() for attr in self.user_display_custom_attributes.split(",")]

    def get_user_search_attributes_list(self) -> List[str]:
        """Get user search attributes as a list."""
        if self.user_search_custom_attributes:
            return [attr.strip() for attr in self.user_search_custom_attributes.split(",")]
        return []

    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.env == "development"

    def is_test(self) -> bool:
        """Check if running in test mode."""
        return self.env == "test"

    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not (self.is_development() or self.is_test())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Create and cache application settings.

    Returns:
        Settings: Cached settings instance
    """
    return Settings()


# Convenience function for accessing settings
settings = get_settings()
