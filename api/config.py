import json
import os

ENV = os.getenv("FLASK_ENV")
DEBUG = ENV == "development"

CLIENT_ORIGIN_URL = os.getenv("CLIENT_ORIGIN_URL")

OKTA_DOMAIN = os.getenv("OKTA_DOMAIN")
OKTA_API_TOKEN = os.getenv("OKTA_API_TOKEN")
# The Group Owners API is only available to Okta plans with IGA enabled
# Disable by default, but allow opt-in to sync group owners to Okta if desired
OKTA_USE_GROUP_OWNERS_API = os.getenv("OKTA_USE_GROUP_OWNERS_API", "False") == "True"
CURRENT_OKTA_USER_EMAIL = os.getenv("CURRENT_OKTA_USER_EMAIL", "wumpus@discord.com")

# Optional env var to set a custom Okta Group Profile attribute for Access management inclusion/exclusion
OKTA_GROUP_PROFILE_CUSTOM_ATTR = os.getenv("OKTA_GROUP_PROFILE_CUSTOM_ATTR")

SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URI")
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ECHO = ENV == "development"  # or ENV == "test"

# Attributes to display in the user page
USER_DISPLAY_CUSTOM_ATTRIBUTES = os.getenv("USER_DISPLAY_CUSTOM_ATTRIBUTES", "Title,Manager")
MAX_ACCESS_REQUEST_AGE_SECONDS = os.getenv("MAX_ACCESS_REQUEST_AGE_SECONDS", 7 * 24 * 60 * 60)  # 7 days


def default_user_search() -> list[str]:
    default_user_attrs = USER_DISPLAY_CUSTOM_ATTRIBUTES.split(",")
    if "Manager" in default_user_attrs:
        default_user_attrs.remove("Manager")
    return default_user_attrs


# Attributes to search across for users
# defaults to the USER_DISPLAY_CUSTOM_ATTRIBUTES excluding the "Manager" attribute
USER_SEARCH_CUSTOM_ATTRIBUTES = os.getenv("USER_SEARCH_CUSTOM_ATTRIBUTES", ",".join(default_user_search()))

# Cloudflare Access authentication
# The Application Audience (AUD) tag for your application
if os.getenv("CLOUDFLARE_APPLICATION_AUDIENCE") is not None:
    CLOUDFLARE_APPLICATION_AUDIENCE = os.getenv("CLOUDFLARE_APPLICATION_AUDIENCE")
# Your Cloudflare Access team domain
if os.getenv("CLOUDFLARE_TEAM_DOMAIN") is not None:
    CLOUDFLARE_TEAM_DOMAIN = os.getenv("CLOUDFLARE_TEAM_DOMAIN")

# OIDC authentication
# Specify an OIDC client secret json blob or path to a json file
if os.getenv("OIDC_CLIENT_SECRETS") is not None:
    OIDC_CLIENT_SECRETS = os.getenv("OIDC_CLIENT_SECRETS")
    assert OIDC_CLIENT_SECRETS is not None
    if OIDC_CLIENT_SECRETS.startswith("{") and OIDC_CLIENT_SECRETS.endswith("}"):
        OIDC_CLIENT_SECRETS = json.loads(OIDC_CLIENT_SECRETS)

# Load some overridable OIDC defaults
OIDC_INTROSPECTION_AUTH_METHOD = os.getenv("OIDC_INTROSPECTION_AUTH_METHOD", "client_secret_post")
OIDC_CLOCK_SKEW = int(os.getenv("OIDC_CLOCK_SKEW", "60"))
if os.getenv("OIDC_OVERWRITE_REDIRECT_URI") is not None:
    OIDC_OVERWRITE_REDIRECT_URI = os.getenv("OIDC_OVERWRITE_REDIRECT_URI")
OIDC_SCOPES = os.getenv("OIDC_SCOPES", "openid email")
if os.getenv("OIDC_SERVER_METADATA_URL") is not None:
    OIDC_SERVER_METADATA_URL = os.getenv("OIDC_SERVER_METADATA_URL")

# SECRET_KEY or SECRET_KEY_FILE is used for encrypting the flask session cookie
# Generate a good secret key with
# `python -c 'import secrets; print(secrets.token_hex())'`
SECRET_KEY_FILE = os.getenv("SECRET_KEY_FILE")
if SECRET_KEY_FILE is None:
    SECRET_KEY = os.getenv("SECRET_KEY")
else:
    with open(SECRET_KEY_FILE, "r") as f:
        SECRET_KEY = f.read().strip()

APP_CREATOR_ID = os.getenv("APP_CREATOR_ID")
OKTA_WEBHOOK_ID = os.getenv("OKTA_WEBHOOK_ID")
OKTA_IGA_ACTOR_ID = os.getenv("OKTA_IGA_ACTOR_ID")

CLOUDSQL_CONNECTION_NAME = os.getenv("CLOUDSQL_CONNECTION_NAME", "")
DATABASE_USER = os.getenv("DATABASE_USER", "root")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "")
DATABASE_NAME = os.getenv("DATABASE_NAME", "access")
DATABASE_USES_PUBLIC_IP = os.getenv("DATABASE_USES_PUBLIC_IP", "False") == "True"

FLASK_SENTRY_DSN = os.getenv("FLASK_SENTRY_DSN")
REACT_SENTRY_DSN = os.getenv("REACT_SENTRY_DSN")

# Add APP_VERSION, defaulting to 'Not Defined' if not set
APP_VERSION = os.getenv("APP_VERSION", "Not Defined")

# Specify a custom app name
APP_NAME = os.getenv("APP_NAME", "Access")
