"""Test data factories.

**ORM model factories** are `polyfactory` `SQLAlchemyFactory` subclasses.
polyfactory otherwise populates *every* column with random data; these factories
instead whitelist just the columns each one sets (via `should_set_field_value` +
`__set_foreign_keys__` / `__set_relationships__` off) and leave the rest — soft
deletes, timestamps, discriminators, FKs, JSON config — to the model/DB defaults.
Explicit `build(...)` kwargs still override any column (managed or not), which
the fixtures and tests rely on.

Two ways to make an instance, both using polyfactory's native API:

- `build(**kwargs)` / `batch(n, **kwargs)` return detached instances without
  touching the DB, so a call site that wants them persisted does its own
  `db.session.add(...)` + `await db.session.commit()`.
- `await create_async(**kwargs)` / `create_batch_async(n, **kwargs)` persist:
  `__async_session__` points at the active request-scoped `db.session`, so
  polyfactory adds, commits, and refreshes the instance — collapsing the
  build/add/commit triad into one call.

**Okta SDK model factories** (`Group`/`User`/`UserSchema`) build the
openapi-generated SDK Pydantic models through `from_dict` (camelCase aliases, the
`anyOf` `profile` union), which polyfactory's generic generation can't reproduce.
They mock external Okta responses rather than our own schemas, so they stay
hand-rolled builders — and keep their own `create`/`create_batch` helpers, which
are unrelated to the polyfactory API above.
"""

from __future__ import annotations

import random
import string
from typing import Any, ClassVar

from faker import Faker
from okta.models.group import Group
from okta.models.user import User
from okta.models.user_schema import UserSchema
from polyfactory import PostGenerated, Use
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory
from polyfactory.field_meta import FieldMeta
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    AppTagMap,
    GroupRequest,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
    RoleRequest,
    Tag,
)

_faker = Faker()


def _okta_id() -> str:
    """A 20-char alphanumeric id shaped like the ids Okta hands out."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=20))


def _rand(length: int = 12) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits + "-", k=length))


def _email(_name: str, values: dict[str, Any], **_kwargs: Any) -> str:
    return "{}.{}@example.com".format(values["first_name"], values["last_name"]).lower()


def _display_name(_name: str, values: dict[str, Any], **_kwargs: Any) -> str:
    return "{} {}".format(values["first_name"], values["last_name"])


class _ORMFactory(SQLAlchemyFactory):
    """Base for the ORM-model factories. Generates only the columns named in
    `_managed`, leaving everything else to the model/DB defaults."""

    __is_base_factory__ = True
    __set_relationships__ = False
    __set_foreign_keys__ = False

    # Resolve the session lazily: the scope (and thus the bound AsyncSession)
    # changes per test, so `create_async` must read `db.session` at call time,
    # not at class-definition time. Only `create_async`/`create_batch_async`
    # use it; `build`/`create` never touch the DB.
    __async_session__ = staticmethod(lambda: db.session)

    # Columns this factory is responsible for generating. Anything not listed
    # (and not passed explicitly by the caller) is left unset.
    _managed: ClassVar[set[str]] = set()

    @classmethod
    def should_set_field_value(cls, field_meta: FieldMeta, **kwargs: Any) -> bool:
        # `super()` returns False for fields the caller passed explicitly, so
        # `build(email=...)` overrides win over generation.
        return field_meta.name in cls._managed and super().should_set_field_value(field_meta, **kwargs)


class OktaUserFactory(_ORMFactory):
    __model__ = OktaUser
    _managed = {"id", "email", "first_name", "last_name", "display_name", "profile"}

    id = Use(_okta_id)
    first_name = Use(_faker.first_name)
    last_name = Use(_faker.last_name)
    email = PostGenerated(_email)
    display_name = PostGenerated(_display_name)
    profile = Use(dict)


class AppFactory(_ORMFactory):
    __model__ = App
    _managed = {"id", "name"}

    id = Use(_okta_id)
    name = Use(lambda: random.choice(string.ascii_uppercase) + _rand())


class OktaGroupFactory(_ORMFactory):
    __model__ = OktaGroup
    _managed = {"id", "name", "type"}

    id = Use(_okta_id)
    name = Use(lambda: random.choice(string.ascii_uppercase) + _rand())
    type = "okta_group"


class RoleGroupFactory(_ORMFactory):
    __model__ = RoleGroup
    _managed = {"id", "name", "type"}

    id = Use(_okta_id)
    name = Use(lambda: RoleGroup.ROLE_GROUP_NAME_PREFIX + random.choice(string.ascii_uppercase) + _rand())
    type = "role_group"


class AppGroupFactory(_ORMFactory):
    __model__ = AppGroup
    # `app_id` / `is_owner` are supplied by the caller.
    _managed = {"id", "name", "type"}

    id = Use(_okta_id)
    name = Use(
        lambda: AppGroup.APP_GROUP_NAME_PREFIX
        + random.choice(string.ascii_uppercase)
        + AppGroup.APP_NAME_GROUP_NAME_SEPARATOR
        + random.choice(string.ascii_uppercase)
        + _rand()
    )
    type = "app_group"


class TagFactory(_ORMFactory):
    __model__ = Tag
    _managed = {"id", "name"}

    id = Use(_okta_id)
    name = Use(lambda: "Tag-" + _rand())


class AccessRequestFactory(_ORMFactory):
    __model__ = AccessRequest
    _managed = {"id", "status", "request_reason"}

    id = Use(_okta_id)
    status = AccessRequestStatus.PENDING
    request_reason = Use(lambda: _faker.paragraph(nb_sentences=5))


class RoleRequestFactory(_ORMFactory):
    __model__ = RoleRequest
    _managed = {"id", "status", "request_reason"}

    id = Use(_okta_id)
    status = AccessRequestStatus.PENDING
    request_reason = Use(lambda: _faker.paragraph(nb_sentences=5))


class GroupRequestFactory(_ORMFactory):
    __model__ = GroupRequest
    # `requester_user_id` is a caller-supplied FK; the rest either generate here
    # or fall back to their column defaults. `requested_group_name` /
    # `requested_group_type` are NOT NULL without a default, so generate them
    # too (an okta_group by default) to keep `create()`/`create_async()`
    # persistable with just a requester.
    _managed = {"id", "status", "request_reason", "requested_group_name", "requested_group_type"}

    id = Use(_okta_id)
    status = AccessRequestStatus.PENDING
    request_reason = Use(lambda: _faker.paragraph(nb_sentences=5))
    requested_group_name = Use(lambda: random.choice(string.ascii_uppercase) + _rand())
    requested_group_type = "okta_group"


# --- Membership / mapping rows ---------------------------------------------
#
# These association tables default every non-FK column (`is_owner` False,
# timestamps to now, `ended_at` null → active, reason ""), and the FKs are
# always caller-supplied, so there is nothing to generate — `_managed` is empty.
# The factories exist for the `create_async` persistence path: `await
# OktaUserGroupMemberFactory.create_async(user_id=u.id, group_id=g.id)` replaces
# the add/commit triad. Pass `ended_at=...` to build an already-expired row.


class OktaUserGroupMemberFactory(_ORMFactory):
    __model__ = OktaUserGroupMember


class RoleGroupMapFactory(_ORMFactory):
    __model__ = RoleGroupMap


class OktaGroupTagMapFactory(_ORMFactory):
    __model__ = OktaGroupTagMap


class AppTagMapFactory(_ORMFactory):
    __model__ = AppTagMap


# ---------------------------------------------------------------------------
# Okta SDK model builders (hand-rolled; see the module docstring).
# ---------------------------------------------------------------------------


class GroupFactory:
    """Builds `okta.models.Group` from a default config, hydrated via `from_dict`
    so the camelCase aliases and `additional_properties` are honored."""

    @staticmethod
    def build(config: dict[str, Any] | None = None, **overrides: Any) -> Group:
        # A caller-supplied `config` replaces the default wholesale; any other
        # kwargs merge on top.
        if config is None:
            config = {
                "id": _faker.pystr(),
                "type": "OKTA_GROUP",
                "created": "2022-02-02T11:54:51.724560Z",
                "lastUpdated": "2022-02-02T11:54:51.724560Z",
                "profile": {"name": _faker.pystr(), "description": _faker.pystr()},
            }
        if overrides:
            config = {**config, **overrides}
        return Group.from_dict(config)

    @staticmethod
    def build_batch(size: int, **overrides: Any) -> list[Group]:
        return [GroupFactory.build(**overrides) for _ in range(size)]

    # factory_boy-style aliases (these SDK models are never persisted).
    create = build
    create_batch = build_batch

    @staticmethod
    async def create_access_owner_group() -> Group:
        access_app = (
            (
                await db.session.scalars(
                    select(App).options(joinedload(App.active_owner_app_groups)).where(App.name == "Access")
                )
            )
            .unique()
            .first()
        )
        assert access_app is not None
        access_owner_group = access_app.active_owner_app_groups[0]
        okta_access_owner_group = GroupFactory.build()
        # ``profile`` is the anyOf union wrapper; reach through actual_instance.
        okta_access_owner_group.profile.actual_instance.name = access_owner_group.name
        okta_access_owner_group.id = access_owner_group.id
        return okta_access_owner_group


class UserFactory:
    """Builds `okta.models.User` from a default config via `from_dict`."""

    @staticmethod
    def build(config: dict[str, Any] | None = None, **overrides: Any) -> User:
        if config is None:
            config = {
                "id": _faker.pystr(),
                "created": "2022-02-02T11:54:51.724560Z",
                "lastUpdated": "2022-02-02T11:54:51.724560Z",
                "type": {"id": "default"},
                "profile": {
                    "login": _faker.pystr(),
                    "firstName": _faker.pystr(),
                    "lastName": _faker.pystr(),
                },
            }
        if overrides:
            config = {**config, **overrides}
        return User.from_dict(config)

    @staticmethod
    def build_batch(size: int, **overrides: Any) -> list[User]:
        return [UserFactory.build(**overrides) for _ in range(size)]

    create = build
    create_batch = build_batch


class UserSchemaFactory:
    """Builds `okta.models.UserSchema` from a default config via `from_dict`."""

    @staticmethod
    def build(config: dict[str, Any] | None = None, **overrides: Any) -> UserSchema:
        if config is not None:
            return UserSchema.from_dict({**config, **overrides} if overrides else config)
        config = {
            "definitions": {
                "base": {
                    "id": "#base",
                    "type": "object",
                    "properties": {
                        "city": {"title": "City"},
                        "costCenter": {"title": "Cost Center"},
                        "countryCode": {"title": "Country Code"},
                        "department": {"title": "Department"},
                        "displayName": {"title": "Display Name"},
                        "division": {"title": "Division"},
                        "email": {"title": "Email"},
                        "employeeNumber": {"title": "Employee Number"},
                        "firstName": {"title": "First Name"},
                        "honorificPrefix": {"title": "HonorificPrefix"},
                        "honorificSuffix": {"title": "HonorificSuffix"},
                        "lastName": {"title": "Last Name"},
                        "locale": {"title": "Locale"},
                        "login": {"title": "Login"},
                        "manager": {"title": "Manager"},
                        "managerId": {"title": "Manager ID"},
                        "middleName": {"title": "Middle Name"},
                        "mobilePhone": {"title": "Mobile Phone"},
                        "nickName": {"title": "Nick Name"},
                        "organization": {"title": "Organization"},
                        "postalAddress": {"title": "Postal Address"},
                        "preferredLanguage": {"title": "Preferred Language"},
                        "primaryPhone": {"title": "Primary Phone"},
                        "profileUrl": {"title": "Profile URL"},
                        "secondEmail": {"title": "Second Email"},
                        "state": {"title": "State"},
                        "streetAddress": {"title": "Street Address"},
                        "timezone": {"title": "Timezone"},
                        "title": {"title": "Title"},
                        "userType": {"title": "User Type"},
                        "zipCode": {"title": "Zip Code"},
                    },
                },
                "custom": {
                    "id": "#custom",
                    "type": "object",
                    "properties": {"custom_attr": {"title": "Custom Attribute"}},
                },
            }
        }
        config.update(overrides)
        return UserSchema.from_dict(config)

    create = build
