##########################################
# External Modules
##########################################

import logging
import sys
import warnings
from importlib.metadata import entry_points
from os import environ
from typing import Optional

from flask import Flask, Response, request
from flask.typing import ResponseReturnValue
from flask_cors import CORS
from flask_talisman import Talisman
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException

from api import manage
from api.authentication import AuthenticationHelpers, CloudflareAuthenticationHelpers
from api.extensions import db, docs, get_cloudsql_conn, migrate, oidc
from api.services import okta
from api.views import (
    access_requests_views,
    apps_views,
    audit_views,
    bugs_views,
    exception_views,
    groups_views,
    health_check_views,
    role_requests_views,
    roles_views,
    tags_views,
    users_views,
    webhook_views,
)


def create_app(testing: Optional[bool] = False) -> Flask:
    ##########################################
    # Flask App Instance
    ##########################################

    app = Flask(
        __name__,
        instance_relative_config=True,
        static_folder="../build",
        static_url_path="",
    )

    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

    app.logger.setLevel(logging.INFO)
    logging.root.setLevel(logging.INFO)

    # Apply log filter to prevent sensitive token information and authorization codes from being logged
    from api.log_filters import TokenSanitizingFilter

    token_filter = TokenSanitizingFilter()
    # Apply filter to specific loggers
    logging.getLogger("flask_oidc").addFilter(token_filter)
    logging.getLogger("werkzeug").addFilter(token_filter)  # For HTTP request logs
    app.logger.addFilter(token_filter)  # For application logs
    logging.root.addFilter(token_filter)  # Catch-all for any other loggers

    logger = logging.getLogger(__name__)

    app.config.from_object("api.config")

    if testing is True:
        app.config["TESTING"] = True
        app.config["ENV"] = "test"

    # Cache the Cloudflare Access public keys for authentication
    if app.config["ENV"] not in ("development", "test"):
        if "CLOUDFLARE_TEAM_DOMAIN" in app.config:
            app.config["CLOUDFLARE_PUBLIC_KEYS"] = CloudflareAuthenticationHelpers.get_public_keys(
                app.config["CLOUDFLARE_TEAM_DOMAIN"]
            )
        elif "OIDC_CLIENT_SECRETS" in app.config:
            if app.config.get("SECRET_KEY", None) is None:
                raise ValueError(
                    "SECRET_KEY must be set in the environment when using OIDC authentication. "
                    + "Generate a good secret key with "
                    + "`python -c 'import secrets; print(secrets.token_hex())'`"
                )

            oidc.init_app(app, prefix="/oidc")
        else:
            raise ValueError(
                "Cloudflare Access (with CLOUDFLARE_TEAM_DOMAIN) or "
                + "OIDC authentication (with OIDC_CLIENT_SECRETS) must "
                + "be configured in a non-development/testing environment"
            )

    ##########################################
    # Error reporting
    ##########################################
    if app.config["ENV"] not in ("development", "test") and "FLASK_SENTRY_DSN" in app.config:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_profile_env_var = environ.get("ENABLE_SENTRY_PROFILER", "0")
        logger.info(f"ENABLE_SENTRY_PROFILER: {sentry_profile_env_var}")

        sentry_sdk.init(
            dsn=app.config["FLASK_SENTRY_DSN"],
            integrations=[FlaskIntegration()],
            environment=app.config["ENV"],
            traces_sample_rate=0.1,
            ignore_errors=[HTTPException, ValidationError],
            profiles_sample_rate=float(sentry_profile_env_var),
        )

    ##########################################
    # HTTP Security Headers
    ##########################################

    csp = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "'unsafe-inline'"],
        "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
        "connect-src": ["'self'", "*.ingest.sentry.io"],
        "worker-src": ["'self'", "blob:"],  # Required for Sentry Session Replay
        "frame-ancestors": ["'none'"],
    }

    Talisman(
        app,
        frame_options="DENY",
        content_security_policy=csp,
        referrer_policy="no-referrer",
        force_https=False,
    )

    okta.initialize(
        app.config["OKTA_DOMAIN"],
        app.config["OKTA_API_TOKEN"],
        use_group_owners_api=app.config["OKTA_USE_GROUP_OWNERS_API"],
    )

    @app.before_request
    def authenticate_request() -> Optional[ResponseReturnValue]:
        # Skip authentication for health check endpoint
        if request.path.startswith("/api/healthz"):
            return None
        return AuthenticationHelpers.authenticate_user(request)

    @app.after_request
    def add_headers(response: Response) -> ResponseReturnValue:
        if request.path.startswith("/api") and not request.path.startswith("/api/swagger-ui"):
            response.headers["X-XSS-Protection"] = "0"
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response

    ##########################################
    # CORS
    ##########################################

    if app.config["ENV"] == "development":
        # Only necessary in development because the React
        # frontend is served from a different port
        CORS(
            app,
            resources={r"/api/*": {"origins": app.config["CLIENT_ORIGIN_URL"]}},
            allow_headers=["Authorization", "Content-Type", "baggage", "sentry-trace"],
            methods=["GET", "POST", "PUT", "DELETE"],
            max_age=86400,
        )

    ##########################################
    # Configure CloudSQL Database Connection
    ##########################################
    if app.config["CLOUDSQL_CONNECTION_NAME"] != "":
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "creator": get_cloudsql_conn(
                cloudsql_connection_name=app.config["CLOUDSQL_CONNECTION_NAME"],
                db_user=app.config["DATABASE_USER"],
                db_name=app.config["DATABASE_NAME"],
                uses_public_ip=app.config["DATABASE_USES_PUBLIC_IP"],
            )
        }

    ##########################################
    # Configure flask extensions
    ##########################################
    db.init_app(app)
    migrate.init_app(app, db)

    ##########################################
    # Configure flask cli commands
    ##########################################
    # Register static commands
    app.cli.add_command(manage.init)
    app.cli.add_command(manage.import_from_okta)
    app.cli.add_command(manage.init_builtin_apps)
    app.cli.add_command(manage.sync)
    app.cli.add_command(manage.fix_unmanaged_groups)
    app.cli.add_command(manage.fix_role_memberships)
    app.cli.add_command(manage.notify)

    # Register dynamically loaded commands
    flask_commands = entry_points(group="flask.commands")

    for entry_point in flask_commands:
        try:
            command = entry_point.load()
            app.cli.add_command(command)
        except Exception as e:
            logger.warning(f"Failed to load command '{entry_point.name}': {e}")

    ###########################################
    # Configure APISpec for swagger support
    ###########################################
    docs.init_app(app)

    ##########################################
    # Blueprint Registration
    ##########################################

    # Ignore the following warning because we are using the same schema name for multiple schemas
    # https://github.com/marshmallow-code/apispec/issues/444
    warnings.filterwarnings("ignore", message="Multiple schemas resolved to the name ")
    # Ignore the following warning because nested schemas may declare less fields via only tuples
    # than the actual schema has specified in the fields tuple
    warnings.filterwarnings("ignore", message="Only explicitly-declared fields will be included in the Schema Object")

    app.register_blueprint(exception_views.bp)
    app.register_blueprint(health_check_views.bp)
    app.register_blueprint(access_requests_views.bp)
    access_requests_views.register_docs()
    app.register_blueprint(apps_views.bp)
    apps_views.register_docs()
    app.register_blueprint(audit_views.bp)
    audit_views.register_docs()
    app.register_blueprint(users_views.bp)
    users_views.register_docs()
    app.register_blueprint(groups_views.bp)
    groups_views.register_docs()
    app.register_blueprint(roles_views.bp)
    roles_views.register_docs()
    app.register_blueprint(role_requests_views.bp)
    role_requests_views.register_docs()
    app.register_blueprint(tags_views.bp)
    tags_views.register_docs()
    app.register_blueprint(webhook_views.bp)
    app.register_blueprint(bugs_views.bp)

    return app
