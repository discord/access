"""FastAPI test harness.

- The `app` fixture returns a `FastAPI` instance.
- The `client` fixture returns an `httpx.AsyncClient` over `ASGITransport`,
  so requests run on the test's own event loop (required: the aiosqlite
  engine is bound to that loop — a sync `TestClient` would drive the app
  from its portal thread's loop and explode with cross-loop futures).
- The `db` fixture binds a sqlite-in-memory aiosqlite engine via
  `db.init_app(...)`, creates tables, and seeds the bootstrap "Access" app
  + admin user.
- The `mock_user` factory fixture overrides
  `app.dependency_overrides[get_current_user_id]` to switch the acting user.
- The `url_for` fixture maps the legacy `"<bp>.<endpoint>"` name passed to
  it onto the FastAPI router's named routes — the existing tests reach for
  this idiom verbatim, so we preserve the call shape.
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator, Callable, Generator
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from api.app import create_app
from api.auth.dependencies import get_current_user_id
from api.config import settings
from api.extensions import Base, Db, _session_scope, db as _db
from api.models import App, AppGroup, OktaUserGroupMember
from tests.factories import (
    AccessRequestFactory,
    AppFactory,
    AppGroupFactory,
    GroupRequestFactory,
    OktaGroupFactory,
    OktaUserFactory,
    RoleGroupFactory,
    RoleRequestFactory,
    TagFactory,
)


# Instance fixtures for the common models. Each returns a freshly built
# (unpersisted) model; tests add it to the session themselves.
@pytest.fixture
def user() -> Any:
    return OktaUserFactory.build()


@pytest.fixture
def okta_group() -> Any:
    return OktaGroupFactory.build()


@pytest.fixture
def role_group() -> Any:
    return RoleGroupFactory.build()


@pytest.fixture
def app_group() -> Any:
    return AppGroupFactory.build()


@pytest.fixture
def access_app() -> Any:
    return AppFactory.build()


@pytest.fixture
def access_request() -> Any:
    return AccessRequestFactory.build()


@pytest.fixture
def role_request() -> Any:
    return RoleRequestFactory.build()


@pytest.fixture
def group_request() -> Any:
    return GroupRequestFactory.build()


@pytest.fixture
def tag() -> Any:
    return TagFactory.build()


def _async_test_uri() -> str:
    """Resolve TEST_DATABASE_URI to an async driver.

    Defaults to in-memory SQLite over aiosqlite. Legacy sync driver names in
    a stale env var keep working (mirrors `api.database.to_async_url`).
    """
    uri = os.environ.get("TEST_DATABASE_URI", "sqlite+aiosqlite://")
    uri = uri.replace("postgresql+pg8000://", "postgresql+asyncpg://")
    if uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    if uri in ("sqlite://", "sqlite:///:memory:"):
        uri = "sqlite+aiosqlite://"
    return uri


@pytest.fixture(scope="session")
def app(request: pytest.FixtureRequest) -> FastAPI:
    # Build the shared app with MCP disabled regardless of the ambient
    # ENABLE_MCP (e.g. a local .env that sets it true). This app is
    # session-scoped; MCP is exercised by test_mcp.py, which builds its own
    # app and manages the singleton. Building the app is not loop-bound
    # (no engine is bound when testing=True and no lifespan without MCP),
    # so a sync session-scoped fixture is safe under per-test event loops.
    prev_mcp = settings.ENABLE_MCP
    settings.ENABLE_MCP = False
    try:
        fastapi_app = create_app(testing=True)
    finally:
        settings.ENABLE_MCP = prev_mcp
    require_descriptions = getattr(request, "param", False)
    settings.REQUIRE_DESCRIPTIONS = require_descriptions
    fastapi_app.state.current_user_email = settings.CURRENT_OKTA_USER_EMAIL
    return fastapi_app


@pytest.fixture
async def db(app: FastAPI) -> AsyncGenerator[Db, None]:
    """Bind a test engine, create tables, and seed bootstrap data.

    Defaults to in-memory SQLite (aiosqlite). Set `TEST_DATABASE_URI` to
    point at any other database (e.g.
    `postgresql+asyncpg://postgres:postgres@localhost:5433/access_test`)
    to verify Postgres-only behaviour.
    """
    db_uri = _async_test_uri()
    if db_uri.startswith("sqlite"):
        # StaticPool shares the single in-memory connection; aiosqlite
        # funnels all operations through one worker thread, so
        # check_same_thread is unnecessary.
        engine = create_async_engine(db_uri, poolclass=StaticPool)
    else:
        engine = create_async_engine(db_uri)
    _db.init_app(engine=engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    token = _session_scope.set("test-session")
    try:
        access_owner = OktaUserFactory.build(email=settings.CURRENT_OKTA_USER_EMAIL)
        access_app = AppFactory.build(
            name=App.ACCESS_APP_RESERVED_NAME,
            description=f"The {App.ACCESS_APP_RESERVED_NAME} Portal",
        )
        access_app_owner_group = AppGroupFactory.build(
            app_id=access_app.id,
            is_owner=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}"
            + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
            description=f"Owners of the {access_app.name} application",
        )
        access_app_owner_group_membership = OktaUserGroupMember(
            user_id=access_owner.id, group_id=access_app_owner_group.id
        )
        _db.session.add(access_owner)
        _db.session.add(access_app)
        _db.session.add(access_app_owner_group)
        _db.session.add(access_app_owner_group_membership)
        await _db.session.commit()

        yield _db
    finally:
        try:
            await _db.session.rollback()
        except Exception:
            pass
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await _db.remove()
        # Close the aiosqlite worker thread / asyncpg connections on the
        # loop that created them.
        await engine.dispose()
        _session_scope.reset(token)


class DatetimeAwareAsyncClient(httpx.AsyncClient):
    """AsyncClient that serializes datetimes in `json=` payloads to ISO strings.

    httpx's stdlib JSON encoder rejects raw datetime objects, so we
    pre-process the payload — many tests pass timezone-aware datetimes
    directly to `client.post(..., json=...)`. All verb helpers funnel
    through `request()`."""

    async def request(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        json_payload = kwargs.get("json")
        if json_payload is not None:
            kwargs["json"] = _stringify_datetimes(json_payload)
        return await super().request(method, url, **kwargs)


def _stringify_datetimes(obj: Any) -> Any:
    from datetime import date, datetime

    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _stringify_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_datetimes(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_stringify_datetimes(v) for v in obj)
    return obj


@pytest.fixture
async def client(app: FastAPI, db: Db) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an in-process async client that shares the test database session.

    `raise_app_exceptions=False` matches the old
    `TestClient(raise_server_exceptions=False)`; `follow_redirects=True`
    matches TestClient's default (httpx defaults to False).
    """
    app.state.current_user_email = settings.CURRENT_OKTA_USER_EMAIL
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with DatetimeAwareAsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=True,
    ) as c:
        yield c
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture
def mock_user(app: FastAPI) -> Generator[Callable[[Any], None], None, None]:
    """Returns a callable that overrides `get_current_user_id` to return the
    given user (or user-id string)."""

    def _set(user_or_id: Any) -> None:
        if hasattr(user_or_id, "id"):
            user_id = user_or_id.id
            email = getattr(user_or_id, "email", None)
            if email:
                app.state.current_user_email = email
        else:
            user_id = user_or_id
        app.dependency_overrides[get_current_user_id] = lambda: user_id

    yield _set
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture
def url_for(app: FastAPI) -> Callable[..., str]:
    """Drop-in replacement for `flask.url_for(<bp>.<endpoint>, **kwargs)`."""

    def _url_for(name: str, **kwargs: Any) -> str:
        endpoint = name.split(".", 1)[1] if "." in name else name
        path_params = {k: str(v) for k, v in kwargs.items() if v is not None}
        try:
            path = app.url_path_for(endpoint, **path_params)
            return str(path)
        except Exception:
            try:
                path = app.url_path_for(endpoint)
                if path_params:
                    return f"{path}?{urlencode(path_params)}"
                return str(path)
            except Exception:
                return f"/{endpoint}"

    return _url_for
