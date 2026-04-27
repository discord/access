"""FastAPI test harness.

Replaces the previous Flask + pytest-flask harness. Key behavior changes:

- The `app` fixture returns a `FastAPI` instance.
- The `client` fixture returns a `fastapi.testclient.TestClient`.
- The `db` fixture binds a sqlite-in-memory engine via `db.init_app(...)`,
  creates tables, and seeds the bootstrap "Access" app + admin user.
- The `mock_user` factory fixture overrides
  `app.dependency_overrides[get_current_user_id]` to switch the acting user.
- The `url_for` fixture mirrors `flask.url_for`'s `"<bp>.<endpoint>"` pattern
  by mapping into FastAPI's named routes.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Generator
from urllib.parse import urlencode

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_factoryboy import register
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api.app import create_app
from api.auth.dependencies import get_current_user_id
from api.config import settings
from api.extensions import _session_scope, db as _db
from api.models import App, AppGroup, OktaUserGroupMember
from tests.factories import (
    AccessRequestFactory,
    AppFactory,
    AppGroupFactory,
    OktaGroupFactory,
    OktaUserFactory,
    RoleGroupFactory,
    RoleRequestFactory,
    TagFactory,
)

register(OktaUserFactory, "user")
register(OktaGroupFactory, "okta_group")
register(RoleGroupFactory, "role_group")
register(AppGroupFactory, "app_group")
register(AppFactory, "access_app")
register(AccessRequestFactory, "access_request")
register(RoleRequestFactory, "role_request")
register(TagFactory, "tag")


@pytest.fixture(scope="session")
def app(request: pytest.FixtureRequest) -> FastAPI:
    load_dotenv(".testenv")
    fastapi_app = create_app(testing=True)
    require_descriptions = getattr(request, "param", False)
    settings.REQUIRE_DESCRIPTIONS = require_descriptions
    fastapi_app.state.current_user_email = settings.CURRENT_OKTA_USER_EMAIL
    return fastapi_app


@pytest.fixture
def db(app: FastAPI) -> Generator[Any, None, None]:
    """Bind a test engine, create tables, and seed bootstrap data.

    Defaults to in-memory SQLite. Set `TEST_DATABASE_URI` to point at any
    other database (e.g. `postgresql+pg8000://postgres:postgres@localhost:5433/access_test`)
    to verify Postgres-only behaviour.
    """
    db_uri = os.environ.get("TEST_DATABASE_URI", "sqlite://")
    if db_uri.startswith("sqlite"):
        engine = create_engine(
            db_uri,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(db_uri)
    _db.init_app(engine=engine)
    _db.drop_all()
    _db.create_all()

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
        _db.session.commit()

        yield _db
    finally:
        try:
            _db.session.rollback()
        except Exception:
            pass
        _db.drop_all()
        _db.remove()
        _session_scope.reset(token)


class _DatetimeAwareTestClient(TestClient):
    """TestClient that serializes datetimes in `json=` payloads to ISO strings.

    The legacy Flask test client tolerated datetime objects in JSON bodies.
    httpx's stdlib JSON encoder does not, so we pre-process the payload."""

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        json_payload = kwargs.get("json")
        if json_payload is not None:
            kwargs["json"] = _stringify_datetimes(json_payload)
        return super().request(method, url, **kwargs)


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
def client(app: FastAPI, db: Any) -> Generator[TestClient, None, None]:
    """Return a FastAPI TestClient that shares the test database session."""
    app.state.current_user_email = settings.CURRENT_OKTA_USER_EMAIL
    with _DatetimeAwareTestClient(app, raise_server_exceptions=False) as c:
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
