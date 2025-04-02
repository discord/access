import random
import string
from typing import Any, Dict

import factory
from factory import fuzzy
from okta.models.group import Group
from okta.models.group_profile import GroupProfile
from okta.models.user import User
from okta.models.user_profile import UserProfile
from okta.models.user_schema import UserSchema
from sqlalchemy.orm import joinedload

from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaUser,
    RoleGroup,
    RoleRequest,
    Tag,
)


class GroupProfileFactory(factory.Factory[GroupProfile]):
    config: Any = factory.Dict(
        {
            "name": factory.Faker("pystr"),
            "description": factory.Faker("pystr"),
            "allow_discord_access": None,  # Default to None for the custom attribute
        }
    )

    class Meta:
        model = GroupProfile


class GroupFactory(factory.Factory[Group]):
    is_deleted: bool = False
    config: Any = factory.Dict(
        {
            "id": factory.Faker("pystr"),
            "name": factory.Faker("pystr"),
            "description": factory.Faker("pystr"),
            "created": "2022-02-02 11:54:51.724560",
            "last_updated": "2022-02-02 11:54:51.724560",
            "profile": factory.SubFactory(GroupProfileFactory),
            "type": "OKTA_GROUP",
        }
    )

    class Meta:
        model = Group
        exclude = "is_deleted"

    @staticmethod
    def create_access_owner_group() -> Group:
        access_app = App.query.options(joinedload(App.active_owner_app_groups)).filter(App.name == "Access").first()
        access_owner_group = access_app.active_owner_app_groups[0]
        okta_access_owner_group = GroupFactory.build()
        okta_access_owner_group.profile.name = access_owner_group.name
        okta_access_owner_group.id = access_owner_group.id
        return okta_access_owner_group


class UserProfileFactory(factory.Factory[UserProfile]):
    config: Any = factory.Dict(
        {
            "login": factory.Faker("pystr"),
            "firstName": factory.Faker("pystr"),
            "lastName": factory.Faker("pystr"),
        }
    )

    class Meta:
        model = UserProfile


class UserFactory(factory.Factory[User]):
    is_deleted: bool = False
    config: Any = factory.Dict(
        {
            "id": factory.Faker("pystr"),
            "login": factory.Faker("pystr"),
            "created": "2022-02-02 11:54:51.724560",
            "last_updated": "2022-02-02 11:54:51.724560",
            "profile": factory.SubFactory(UserProfileFactory),
            "status": factory.Maybe(
                "..is_deleted",
                yes_declaration="DEPROVISIONED",
                no_declaration=None,
            ),
            "statusChanged": factory.Maybe(
                "..is_deleted",
                yes_declaration="2022-06-02 11:54:51.724560",
                no_declaration=None,
            ),
            "type": factory.Dict({"id": "default"}),
        }
    )

    class Meta:
        model = User
        exclude = "is_deleted"


class UserSchemaFactory(factory.Factory[UserSchema]):
    config: Any = factory.Dict(
        {
            "definitions": factory.Dict(
                {
                    "base": factory.Dict(
                        {
                            "properties": factory.Dict(
                                {
                                    "city": factory.Dict({"title": "City"}),
                                    "costCenter": factory.Dict({"title": "Cost Center"}),
                                    "countryCode": factory.Dict({"title": "Country Code"}),
                                    "department": factory.Dict({"title": "Department"}),
                                    "displayName": factory.Dict({"title": "Display Name"}),
                                    "division": factory.Dict({"title": "Division"}),
                                    "email": factory.Dict({"title": "Email"}),
                                    "employeeNumber": factory.Dict({"title": "Employee Number"}),
                                    "firstName": factory.Dict({"title": "First Name"}),
                                    "honorificPrefix": factory.Dict({"title": "HonorificPrefix"}),
                                    "honorificSuffix": factory.Dict({"title": "HonorificSuffix"}),
                                    "lastName": factory.Dict({"title": "Last Name"}),
                                    "locale": factory.Dict({"title": "Locale"}),
                                    "login": factory.Dict({"title": "Login"}),
                                    "manager": factory.Dict({"title": "Manager"}),
                                    "managerId": factory.Dict({"title": "Manager ID"}),
                                    "middleName": factory.Dict({"title": "Middle Name"}),
                                    "mobilePhone": factory.Dict({"title": "Mobile Phone"}),
                                    "nickName": factory.Dict({"title": "Nick Name"}),
                                    "organization": factory.Dict({"title": "Organization"}),
                                    "postalAddress": factory.Dict({"title": "Postal Address"}),
                                    "preferredLanguage": factory.Dict({"title": "Preferred Language"}),
                                    "primaryPhone": factory.Dict({"title": "Primary Phone"}),
                                    "profileUrl": factory.Dict({"title": "Profile URL"}),
                                    "secondEmail": factory.Dict({"title": "Second Email"}),
                                    "state": factory.Dict({"title": "State"}),
                                    "streetAddress": factory.Dict({"title": "Street Address"}),
                                    "timezone": factory.Dict({"title": "Timezone"}),
                                    "title": factory.Dict({"title": "Title"}),
                                    "userType": factory.Dict({"title": "User Type"}),
                                    "zipCode": factory.Dict({"title": "Zip Code"}),
                                }
                            )
                        }
                    ),
                    "custom": factory.Dict(
                        {
                            "properties": factory.Dict(
                                {
                                    "custom_attr": factory.Dict({"title": "Custom Attribute"}),
                                }
                            )
                        }
                    ),
                }
            )
        }
    )

    class Meta:
        model = UserSchema


class OktaUserFactory(factory.Factory[OktaUser]):
    id: factory.Faker = factory.Faker("pystr")
    email: factory.LazyAttribute = factory.LazyAttribute(
        lambda a: "{}.{}@example.com".format(a.first_name, a.last_name).lower()
    )
    first_name: factory.Faker = factory.Faker("first_name")
    last_name: factory.Faker = factory.Faker("last_name")
    display_name: factory.LazyAttribute = factory.LazyAttribute(lambda a: "{} {}".format(a.first_name, a.last_name))
    profile: Dict[str, str] = {}

    class Meta:
        model = OktaUser


class AppFactory(factory.Factory[App]):
    id: factory.Faker = factory.Faker("pystr")
    name: fuzzy.FuzzyText = fuzzy.FuzzyText(
        length=12, prefix=random.choice(string.ascii_uppercase), chars=string.ascii_letters + string.digits + "-"
    )

    class Meta:
        model = App


class OktaGroupFactory(factory.Factory[OktaGroup]):
    id: factory.Faker = factory.Faker("pystr")
    name: fuzzy.FuzzyText = fuzzy.FuzzyText(
        length=12,
        prefix=random.choice(string.ascii_uppercase),
        chars=string.ascii_letters + string.digits + "-",
    )
    type: str = "okta_group"

    class Meta:
        model = OktaGroup


class RoleGroupFactory(factory.Factory[RoleGroup]):
    id: factory.Faker = factory.Faker("pystr")
    name: fuzzy.FuzzyText = fuzzy.FuzzyText(
        length=12,
        prefix=RoleGroup.ROLE_GROUP_NAME_PREFIX + random.choice(string.ascii_uppercase),
        chars=string.ascii_letters + string.digits + "-",
    )
    type: str = "role_group"

    class Meta:
        model = RoleGroup


class AppGroupFactory(factory.Factory[AppGroup]):
    id: factory.Faker = factory.Faker("pystr")
    name: fuzzy.FuzzyText = fuzzy.FuzzyText(
        length=12,
        prefix=AppGroup.APP_GROUP_NAME_PREFIX
        + random.choice(string.ascii_uppercase)
        + AppGroup.APP_NAME_GROUP_NAME_SEPARATOR
        + random.choice(string.ascii_uppercase),
        chars=string.ascii_letters + string.digits + "-",
    )
    type: str = "app_group"

    class Meta:
        model = AppGroup


class AccessRequestFactory(factory.Factory[AccessRequest]):
    id: factory.Faker = factory.Faker("pystr")
    status: AccessRequestStatus = AccessRequestStatus.PENDING
    request_reason: factory.Faker = factory.Faker("paragraph", nb_sentences=5)

    class Meta:
        model = AccessRequest


class RoleRequestFactory(factory.Factory[RoleRequest]):
    id: factory.Faker = factory.Faker("pystr")
    status: AccessRequestStatus = AccessRequestStatus.PENDING
    request_reason: factory.Faker = factory.Faker("paragraph", nb_sentences=5)

    class Meta:
        model = RoleRequest


class TagFactory(factory.Factory[Tag]):
    id: factory.Faker = factory.Faker("pystr")
    name: fuzzy.FuzzyText = fuzzy.FuzzyText(length=12, prefix="Tag-", chars=string.ascii_letters + string.digits + "-")

    class Meta:
        model = Tag
