from typing import Generator

import pytest
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from pytest_factoryboy import register

from api.app import create_app
from api.extensions import db as _db
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
def app() -> Flask:
    load_dotenv(".testenv")
    app = create_app(testing=True)
    return app


@pytest.fixture
def db(app: Flask) -> Generator[SQLAlchemy, None, None]:
    # Drop the data at the beginning of the test to guarantee
    # a clean database, and to allow DB inspection after a test run.
    _db.drop_all()
    _db.app = app

    with app.app_context():
        _db.create_all()

    access_owner = OktaUserFactory.build(email=app.config["CURRENT_OKTA_USER_EMAIL"])
    access_app = AppFactory.build(
        name=App.ACCESS_APP_RESERVED_NAME, description=f"The {App.ACCESS_APP_RESERVED_NAME} Portal"
    )
    access_app_owner_group = AppGroupFactory.build(
        app_id=access_app.id,
        is_owner=True,
        name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{access_app.name}"
        + f"{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        description=f"Owners of the {access_app.name} application",
    )
    access_app_owner_group_membership = OktaUserGroupMember(user_id=access_owner.id, group_id=access_app_owner_group.id)
    _db.session.add(access_owner)
    _db.session.add(access_app)
    _db.session.add(access_app_owner_group)
    _db.session.add(access_app_owner_group_membership)
    _db.session.commit()

    yield _db

    _db.session.close()
