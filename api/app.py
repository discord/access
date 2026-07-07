"""FastAPI application factory.

Wires the database engine, middleware stack, exception handlers, routers,
and the React SPA static files. The OIDC and Cloudflare Access auth
subsystems are configured here when the relevant environment variables are
present.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from os import environ
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from api import exception_handlers, middleware
from api.config import settings
from api.database import build_async_engine
from api.extensions import db
from api.log_filters import RedactingUvicornLogger, TokenSanitizingFilter
from api.schemas.core_schemas import ProblemDetail
from api.services import okta

logger = logging.getLogger(__name__)

BUILD_DIR = Path(__file__).resolve().parent.parent / "build"


def _operation_id_from_route_name(route: APIRoute) -> str:
    """Derive each operation's OpenAPI ``operationId`` from its explicit ``name=``.

    FastAPI's default ``generate_unique_id`` mangles the path and method into
    the id (``apps_api_apps_get``), which the OpenAPI → TypeScript codegen turns
    into ugly hook names. Every API route already sets a unique, semantic
    ``name=`` (``apps``, ``app_by_id``, ``apps_create``, …), so using it verbatim
    yields clean generated hooks (``useApps``, ``useAppById``, ``useAppsCreate``)."""
    return route.name


# Shared RFC 9457 error response, advertised on every router so the generated
# TypeScript client types its ``*Error`` payloads against ``ProblemDetail``
# instead of falling back to the success schema. The runtime envelope is built
# in ``api/exception_handlers.py``.
DEFAULT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {
        "model": ProblemDetail,
        "description": "RFC 9457 problem-detail error response.",
    },
}


def _flatten_query_param_models(openapi_schema: dict[str, Any]) -> None:
    """Expand object-typed query parameters into one param per model field.

    Our list endpoints take their filters as a Pydantic model
    (``q_args: Annotated[SearchAppQuery, Query()]``), which FastAPI normally
    flattens into individual query params (``?q=…``). ``fastapi_pagination``'s
    ``add_pagination`` rewrites the route signature in a way that defeats that
    flattening, leaving a single object-typed ``q_args`` param (a ``$ref``) in
    the spec — even though at runtime FastAPI still reads the flat params. That
    misleads the OpenAPI→TypeScript codegen into emitting a nested, wrongly
    serialized ``q_args`` object. Rewrite the spec to match the wire: replace
    each object-schema query param with one param per field of the referenced
    model.
    """
    schemas = openapi_schema.get("components", {}).get("schemas", {})
    for path_item in openapi_schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "parameters" not in operation:
                continue
            flattened: list[dict[str, Any]] = []
            for param in operation["parameters"]:
                ref = param.get("schema", {}).get("$ref") if param.get("in") == "query" else None
                model = schemas.get(ref.rsplit("/", 1)[-1]) if ref else None
                props = model.get("properties") if model else None
                if model and props:
                    required = set(model.get("required", []))
                    for field_name, field_schema in props.items():
                        flattened.append(
                            {
                                "name": field_name,
                                "in": "query",
                                "required": field_name in required,
                                "schema": field_schema,
                            }
                        )
                else:
                    flattened.append(param)
            operation["parameters"] = flattened


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    logging.root.setLevel(logging.INFO)
    token_filter = TokenSanitizingFilter()
    logging.getLogger("authlib").addFilter(token_filter)
    logging.getLogger("uvicorn.access").addFilter(token_filter)
    logging.getLogger("uvicorn.access").addFilter(RedactingUvicornLogger())
    logging.root.addFilter(token_filter)


def _configure_sentry() -> None:
    if settings.ENV in ("development", "test"):
        return
    if not settings.FASTAPI_SENTRY_DSN:
        return
    import sentry_sdk
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from api.auth.dependencies import OIDCRedirectRequired

    sentry_profile_env_var = environ.get("ENABLE_SENTRY_PROFILER", "0")
    logger.info(f"ENABLE_SENTRY_PROFILER: {sentry_profile_env_var}")
    sentry_sdk.init(
        dsn=settings.FASTAPI_SENTRY_DSN,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        environment=settings.ENV,
        traces_sample_rate=0.1,
        profiles_sample_rate=float(sentry_profile_env_var),
        # Don't ship 4xx-class control flow to Sentry — those are user error,
        # not application error, and request bodies in breadcrumbs may
        # carry PII. OIDCRedirectRequired is the unauthenticated-OIDC
        # path that the exception handler converts to a 307; that's
        # control flow too, not an exception worth alerting on.
        ignore_errors=[
            HTTPException,
            StarletteHTTPException,
            RequestValidationError,
            ValidationError,
            OIDCRedirectRequired,
        ],
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


def _configure_threadpool_limit() -> None:
    """Cap the anyio worker-thread pool.

    Under the async stack, route handlers run on the event loop, so this no
    longer bounds request concurrency. But anyio's threadpool still backs the
    paths that offload to threads (`anyio.to_thread.run_sync`, any sync
    dependencies), and its default limit is 40 threads per event loop; bounding
    it keeps those from fanning out without limit. ``THREADPOOL_MAX_WORKERS``
    of 0 leaves anyio's default untouched. Must be called from within the
    running event loop, since the limiter is a loop-scoped anyio ``RunVar``.
    """
    if settings.THREADPOOL_MAX_WORKERS <= 0:
        return
    import anyio.to_thread

    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = settings.THREADPOOL_MAX_WORKERS
    logger.info("Worker-thread pool limit set to %d", settings.THREADPOOL_MAX_WORKERS)


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

    # MCP lifespan: when ENABLE_MCP is on, build the server eagerly so
    # the route registration below can grab the session manager, then
    # enter its lifespan in the FastAPI lifespan so the session
    # manager's task group is running while requests are served.
    mcp_lifespan: Optional[Any] = None
    if settings.ENABLE_MCP:
        from api.mcp.server import create_mcp_server
        from api.mcp.server import mcp_lifespan as mcp_lifespan

        create_mcp_server()

    @asynccontextmanager
    async def lifespan(_fast_app: FastAPI) -> AsyncIterator[None]:
        # Bound the worker-thread pool inside the running event loop (the anyio
        # limiter is loop-scoped, so this can't run at import time).
        _configure_threadpool_limit()
        # Hold one Okta client (and its pooled aiohttp connector) for the life of
        # the server loop so request handlers reuse it instead of building a
        # session per Okta call. No-op when Okta isn't configured.
        await okta.start_pooled_client()
        try:
            if mcp_lifespan is not None:
                async with mcp_lifespan():
                    yield
            else:
                yield
        finally:
            await okta.stop_pooled_client()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.DEBUG else None,
        # Defense-in-depth: every endpoint goes through the auth gate.
        # `require_authenticated` short-circuits for the small allowlist
        # (health, OIDC login). Endpoints still declare `CurrentUserId`
        # for the user-id value; the dependency is deduped at runtime.
        # The SPA catch-all route mounted below inherits this dependency
        # too, so static assets are inside the gate.
        dependencies=[Depends(require_authenticated)],
        lifespan=lifespan,
        # Clean operationIds (from each route's `name=`) so the generated
        # TypeScript client gets readable hook names. See the function docstring.
        generate_unique_id_function=_operation_id_from_route_name,
    )

    # Bind the SQLAlchemy engine to the session facade. In tests the `db`
    # fixture rebuilds with a sqlite-in-memory engine, so we only bind here
    # when not testing.
    if not testing and (settings.SQLALCHEMY_DATABASE_URI or settings.CLOUDSQL_CONNECTION_NAME):
        db.init_app(engine=build_async_engine())

    # OIDC: Authlib + SessionMiddleware. Only mounted if configured.
    if settings.OIDC_CLIENT_SECRETS is not None and settings.SECRET_KEY:
        from starlette.middleware.sessions import SessionMiddleware
        from api.auth.oidc import register_oidc

        # HttpOnly is set by SessionMiddleware unconditionally; we add Secure
        # outside dev and pin SameSite to lax so the IdP redirect to
        # /oidc/authorize can still read the session.
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.SECRET_KEY,
            https_only=settings.ENV != "development",
            same_site="lax",
        )
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

    # Order: outer-most last. RequestObservability outermost so it times the
    # full request; RequestId next so request_id is on state for inner
    # middleware and dependencies. The MCP auth middleware is added BEFORE
    # RequestContextMiddleware so it ends up *inside* that wrapper —
    # RequestContext sets source="web" first, then MCP auth overrides to
    # "mcp" for the duration of /mcp requests (and clears on the way out).
    # Inverting the order would let RequestContext trample the MCP-sourced
    # binding before tools see it.
    app.add_middleware(middleware.CacheControlMiddleware)
    app.add_middleware(middleware.SecurityHeadersMiddleware)
    if settings.ENABLE_MCP:
        from api.mcp.server import (
            MCPAuthMiddleware,
            get_mcp_route,
            get_protected_resource_metadata_routes,
        )

        app.add_middleware(MCPAuthMiddleware)
        # Use a Route (not a Mount) so /mcp without trailing slash works;
        # /mcp/ would 405 some MCP clients. Insert the route at the
        # FastAPI level — it doesn't go through include_router because
        # the handler is a raw ASGI app from FastMCP.
        app.routes.append(get_mcp_route())
        # RFC 9728 metadata, served unauthenticated for client discovery.
        # Registered ahead of the SPA catch-all so the well-known path
        # resolves here; MCP auth only intercepts /mcp, so it stays public.
        app.routes.extend(get_protected_resource_metadata_routes())
    app.add_middleware(middleware.RequestContextMiddleware)
    app.add_middleware(middleware.RequestIdMiddleware)
    app.add_middleware(middleware.RequestObservabilityMiddleware)

    # Host-header validation. Added last so it is the outermost layer and
    # rejects a spoofed Host before anything else runs. Enabled for anyone who
    # sets ALLOWED_HOSTS (defense-in-depth for any Host-derived URL). Hard-
    # required only for OIDC deployments outside dev/test, since the OIDC login
    # flow derives its redirect_uri from the Host header (api/auth/oidc.py) and
    # a spoofed Host can poison the IdP callback. Cloudflare deployments don't
    # hit that path, so they aren't forced to set it.
    oidc_configured = settings.OIDC_CLIENT_SECRETS is not None and settings.SECRET_KEY
    if settings.trusted_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    elif oidc_configured and settings.ENV not in ("development", "test"):
        raise ValueError(
            "ALLOWED_HOSTS must be set (a comma-separated Host header allowlist) "
            "for an OIDC deployment outside development/test so the OIDC "
            "redirect_uri cannot be poisoned via a spoofed Host header"
        )

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

    app.include_router(health.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(access_requests.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(apps.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(audit.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(bugs.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(group_requests.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(groups.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(plugins.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(role_requests.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(roles.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(tags.router, responses=DEFAULT_ERROR_RESPONSES)
    app.include_router(users.router, responses=DEFAULT_ERROR_RESPONSES)

    # Wires up `fastapi_pagination` so the `Page[T]` return types resolve their
    # `Params` dependency from query string. Must be after `include_router`.
    from fastapi_pagination import add_pagination

    add_pagination(app)

    # Patch OpenAPI generation to undo `add_pagination`'s flattening regression
    # on Pydantic-model query params (see `_flatten_query_param_models`). Wraps
    # the default `app.openapi`, mutating the (cached) schema in place.
    _default_openapi = app.openapi

    def _patched_openapi() -> dict[str, Any]:
        schema = _default_openapi()
        _flatten_query_param_models(schema)
        return schema

    app.openapi = _patched_openapi  # type: ignore[method-assign]

    # SPA: serve `build/` from a catch-all FastAPI route so static assets
    # go through the app-wide `require_authenticated` dependency (an
    # `app.mount(..., StaticFiles)` would be a Starlette sub-app that
    # bypasses the dependency chain). Registered LAST so the routers
    # above match first.
    if BUILD_DIR.exists():
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        build_dir_resolved = BUILD_DIR.resolve()

        @app.get("/{spa_path:path}", include_in_schema=False, name="spa")
        def serve_spa(spa_path: str) -> FileResponse:
            # Unmapped /api/* paths fall through to here; return a real 404
            # rather than the SPA index.
            if spa_path == "api" or spa_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            # Resolve safely — refuse anything that escapes BUILD_DIR via
            # `..` components.
            try:
                candidate = (BUILD_DIR / spa_path).resolve()
                candidate.relative_to(build_dir_resolved)
            except (ValueError, OSError):
                raise HTTPException(status_code=404, detail="Not Found")
            if candidate.is_file():
                if spa_path.startswith("assets/"):
                    # Vite content-hashes this directory's filenames, so a
                    # given URL can never resolve to different bytes later.
                    # Safe to cache indefinitely.
                    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
                else:
                    headers = {}
                return FileResponse(candidate, headers=headers)
            if spa_path.startswith("assets/"):
                # Everything under assets/ is a content-hashed build
                # artifact. If it's not on disk it's genuinely missing (e.g.
                # a request that landed on a pod running a different build
                # during a rolling deploy), not a client-side route for
                # React Router to handle, and it must not be cached as a
                # false negative once the file is actually available again.
                raise HTTPException(status_code=404, detail="Not Found", headers={"Cache-Control": "no-store"})
            # Anything else falls back to index.html so React Router can
            # handle the path on the client. This response must never be
            # cached: it's the only thing that says which asset hashes are
            # currently valid, and a cached copy could keep pointing at
            # hashes a future deploy has already removed.
            return FileResponse(BUILD_DIR / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})

    return app
