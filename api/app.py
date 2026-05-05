"""FastAPI application factory.

Wires the database engine, middleware stack, exception handlers, routers,
and the React SPA static files. The OIDC and Cloudflare Access auth
subsystems are configured here when the relevant environment variables are
present.
"""

from __future__ import annotations

import logging
import sys
from os import environ
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from api import exception_handlers, middleware
from api.config import settings
from api.database import build_engine
from api.extensions import db
from api.log_filters import TokenSanitizingFilter
from api.services import okta

logger = logging.getLogger(__name__)

BUILD_DIR = Path(__file__).resolve().parent.parent / "build"


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    logging.root.setLevel(logging.INFO)
    token_filter = TokenSanitizingFilter()
    logging.getLogger("authlib").addFilter(token_filter)
    logging.getLogger("uvicorn.access").addFilter(token_filter)
    logging.root.addFilter(token_filter)


def _configure_sentry() -> None:
    if settings.ENV in ("development", "test"):
        return
    if not settings.FLASK_SENTRY_DSN:
        return
    import sentry_sdk
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from starlette.exceptions import HTTPException as StarletteHTTPException

    sentry_profile_env_var = environ.get("ENABLE_SENTRY_PROFILER", "0")
    logger.info(f"ENABLE_SENTRY_PROFILER: {sentry_profile_env_var}")
    sentry_sdk.init(
        dsn=settings.FLASK_SENTRY_DSN,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        environment=settings.ENV,
        traces_sample_rate=0.1,
        profiles_sample_rate=float(sentry_profile_env_var),
        # Don't ship 4xx-class control flow to Sentry — those are user error,
        # not application error, and request bodies in breadcrumbs may
        # carry PII.
        ignore_errors=[HTTPException, StarletteHTTPException, RequestValidationError, ValidationError],
    )


def _validate_plugins() -> None:
    try:
        from api.plugins import load_plugins
        from api.plugins.app_group_lifecycle import get_app_group_lifecycle_plugins

        load_plugins()
        # Validate per-app config metadata for any registered app group
        # lifecycle plugins. The other plugin types don't have a
        # corresponding metadata-validation step.
        _ = get_app_group_lifecycle_plugins()
    except Exception:
        logger.exception("Failed to validate plugins.")
        raise


def _configure_okta() -> None:
    if settings.OKTA_DOMAIN and settings.OKTA_API_TOKEN:
        okta.initialize(
            settings.OKTA_DOMAIN,
            settings.OKTA_API_TOKEN,
            use_group_owners_api=settings.OKTA_USE_GROUP_OWNERS_API,
        )


def create_app(testing: Optional[bool] = False) -> FastAPI:
    _configure_logging()

    if testing:
        # Tests run in-process with an in-memory SQLite database created fresh
        # by the `db` fixture. Setting ENV here keeps the auth dependency in
        # the dev/test bypass branch.
        settings.ENV = "test"
    else:
        # Crash on a missing ENV env-var so deployments cannot silently fall
        # through to dev mode (which exposes /api/docs and disables auth).
        from api.config import assert_env_explicitly_set

        assert_env_explicitly_set()

    _configure_sentry()
    _configure_okta()
    _validate_plugins()

    from fastapi import Depends

    from api.auth.dependencies import require_authenticated

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.DEBUG else None,
        # Defense-in-depth: every endpoint goes through the auth gate.
        # `require_authenticated` short-circuits for the small allowlist
        # (health, OIDC login). Endpoints that need the user-id value still
        # declare `CurrentUserId`; the dependency is deduped at runtime.
        dependencies=[Depends(require_authenticated)],
    )

    # Bind the SQLAlchemy engine to the shim. In tests the `db` fixture
    # rebuilds with a sqlite-in-memory engine, so we only bind here when not
    # testing.
    if not testing and (settings.SQLALCHEMY_DATABASE_URI or settings.CLOUDSQL_CONNECTION_NAME):
        db.init_app(engine=build_engine())

    # OIDC: Authlib + SessionMiddleware. Only mounted if configured.
    if settings.OIDC_CLIENT_SECRETS is not None and settings.SECRET_KEY:
        from starlette.middleware.sessions import SessionMiddleware
        from api.auth.oidc import register_oidc

        app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)
        register_oidc(app)
    elif settings.ENV not in ("development", "test"):
        if not settings.CLOUDFLARE_TEAM_DOMAIN:
            raise ValueError(
                "Cloudflare Access (with CLOUDFLARE_TEAM_DOMAIN) or OIDC "
                "authentication (with OIDC_CLIENT_SECRETS) must be configured "
                "in a non-development/testing environment"
            )

    # CORS (only when an explicit origin is configured)
    if settings.CLIENT_ORIGIN_URL:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.CLIENT_ORIGIN_URL],
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )

    # Order: outer-most last. RequestId outermost so request_id is on state
    # for inner middleware and dependencies.
    app.add_middleware(middleware.CacheControlMiddleware)
    app.add_middleware(middleware.SecurityHeadersMiddleware)
    app.add_middleware(middleware.RequestContextMiddleware)
    app.add_middleware(middleware.RequestIdMiddleware)

    exception_handlers.install(app)

    # Routers
    from api.routers import (
        access_requests,
        apps,
        audit,
        bugs,
        group_requests,
        groups,
        health,
        plugins,
        role_requests,
        roles,
        tags,
        users,
    )

    app.include_router(health.router)
    app.include_router(access_requests.router)
    app.include_router(apps.router)
    app.include_router(audit.router)
    app.include_router(bugs.router)
    app.include_router(group_requests.router)
    app.include_router(groups.router)
    app.include_router(plugins.router)
    app.include_router(role_requests.router)
    app.include_router(roles.router)
    app.include_router(tags.router)
    app.include_router(users.router)

    # SPA: mount build/ at root so React routing works. Mount LAST so API
    # routes win.
    if BUILD_DIR.exists():
        app.mount("/", StaticFiles(directory=str(BUILD_DIR), html=True), name="spa")

    return app
