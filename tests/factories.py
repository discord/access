import random
import string
from typing import Dict

import factory
from factory import fuzzy
from okta.models.group import Group
from okta.models.group_profile import GroupProfile
from okta.models.user import User
from okta.models.user_profile import UserProfile
from okta.models.user_schema import UserSchema
from sqlalchemy.orm import joinedload

from api.models import AccessRequest, AccessRequestStatus, App, AppGroup, OktaGroup, OktaUser, RoleGroup, Tag


class GroupProfileFactory(factory.Factory):
    config = factory.Dict(
        {
            "name": factory.Faker("pystr"),
            "description": factory.Faker("pystr"),
        }
    )

    class Meta:
        model = GroupProfile


class GroupFactory(factory.Factory):
    is_deleted = False
    config = factory.Dict(
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


class UserProfileFactory(factory.Factory):
    config = factory.Dict(
        {
            "login": factory.Faker("pystr"),
            "firstName": factory.Faker("pystr"),
            "lastName": factory.Faker("pystr"),
        }
    )

    class Meta:
        model = UserProfile


class UserFactory(factory.Factory):
    is_deleted = False
    config = factory.Dict(
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


class UserSchemaFactory(factory.Factory):
    config = factory.Dict(
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


class OktaUserFactory(factory.Factory):
    id = factory.Faker("pystr")

    email = factory.LazyAttribute(lambda a: "{}.{}@example.com".format(a.first_name, a.last_name).lower())
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    display_name = factory.LazyAttribute(lambda a: "{} {}".format(a.first_name, a.last_name))
    profile: Dict[str, str] = {}

    class Meta:
        model = OktaUser


class AppFactory(factory.Factory):
    id = factory.Faker("pystr")
    name = fuzzy.FuzzyText(
        length=12, prefix=random.choice(string.ascii_uppercase), chars=string.ascii_letters + string.digits + "-"
    )

    class Meta:
        model = App


class OktaGroupFactory(factory.Factory):
    id = factory.Faker("pystr")
    name = fuzzy.FuzzyText(
        length=12,
        prefix=random.choice(string.ascii_uppercase),
        chars=string.ascii_letters + string.digits + "-",
    )
    type = "okta_group"

    class Meta:
        model = OktaGroup


class RoleGroupFactory(factory.Factory):
    id = factory.Faker("pystr")
    name = fuzzy.FuzzyText(
        length=12,
        prefix=RoleGroup.ROLE_GROUP_NAME_PREFIX + random.choice(string.ascii_uppercase),
        chars=string.ascii_letters + string.digits + "-",
    )
    type = "role_group"

    class Meta:
        model = RoleGroup


class AppGroupFactory(factory.Factory):
    id = factory.Faker("pystr")
    name = fuzzy.FuzzyText(
        length=12,
        prefix=AppGroup.APP_GROUP_NAME_PREFIX
        + random.choice(string.ascii_uppercase)
        + AppGroup.APP_NAME_GROUP_NAME_SEPARATOR
        + random.choice(string.ascii_uppercase),
        chars=string.ascii_letters + string.digits + "-",
    )
    type = "app_group"

    class Meta:
        model = AppGroup


class AccessRequestFactory(factory.Factory):
    id = factory.Faker("pystr")
    status = AccessRequestStatus.PENDING
    request_reason = factory.Faker("paragraph", nb_sentences=5)

    class Meta:
        model = AccessRequest


class TagFactory(factory.Factory):
    id = factory.Faker("pystr")
    name = fuzzy.FuzzyText(length=12, prefix="Tag-", chars=string.ascii_letters + string.digits + "-")

    class Meta:
        model = Tag
