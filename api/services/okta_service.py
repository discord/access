from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Callable, Optional

import dateutil.parser
from okta.client import Client as OktaClient
from okta.models.group import Group as OktaGroupType
from okta.models.group_rule import GroupRule as OktaGroupRuleType
from okta.models.user import User as OktaUserType
from okta.models.user_schema import UserSchema as OktaUserSchemaType

from api.models import OktaGroup, OktaUser

REQUEST_MAX_RETRIES = 3
RETRIABLE_STATUS_CODES = [429, 500, 502, 503, 504]
RETRY_BACKOFF_FACTOR = 0.5


logger = logging.getLogger(__name__)


class OktaService:
    """For interacting with the Okta API"""

    def initialize(self, okta_domain: Optional[str], okta_api_token: Optional[str]) -> None:
        # Ignore an okta domain and api token when testing
        if okta_domain is None or okta_api_token is None:
            return
        self.okta_domain = okta_domain
        self.okta_api_token = okta_api_token
        self.okta_client = OktaClient({
                "orgUrl": f"https://{okta_domain}",
                "token": okta_api_token,
            }
        )

    @staticmethod
    async def _retry(func: Callable[[Any], Any], *args: Any, **kwargs: Any) -> Any:
        """Retry Okta API requests with specific status codes using exponential backoff."""
        for attempt in range(1 + REQUEST_MAX_RETRIES):
            result = await func(*args, **kwargs)

            if len(result) == 2:
                response, error = result
            elif len(result) == 3:
                _, response, error = result
            else:
                raise Exception("Unexpected result structure from Okta client.")

            if (attempt == REQUEST_MAX_RETRIES or 
                error is None or 
                response is None or 
                (response is not None and response.get_status() not in RETRIABLE_STATUS_CODES)):
                return result

            if response is None:
                logger.warning('Got None response from Okta resource. Retrying...')
            else:
                logger.warning(f'Got {response.get_status()} response from Okta resource {response._url}, with error:'
                            f' {error}. Retrying...'
                )

            await asyncio.sleep(RETRY_BACKOFF_FACTOR * (2**attempt))

    def get_user(self, userId: str) -> User:
        user, _, error = asyncio.run(OktaService._retry(self.okta_client.get_user, userId))

        if error is not None:
            raise Exception(error)
        assert user is not None

        return User(user)

    def get_user_schema(self, userTypeId: str) -> UserSchema:
        async def _get_user_schema(userTypeId: str) -> UserSchema:
            userType, _, error = await OktaService._retry(self.okta_client.get_user_type, userTypeId)

            if error is not None:
                raise Exception(error)
            assert userType is not None

            schemaId = userType.links["schema"]["href"].rsplit("/", 1).pop()

            schema, _, error = await OktaService._retry(self.okta_client.get_user_schema, schemaId)
            if error is not None:
                raise Exception(error)
            return UserSchema(schema)

        return asyncio.run(_get_user_schema(userTypeId))

    def list_users(self) -> list[User]:
        async def _list_users() -> list[User]:
            users, resp, error = await OktaService._retry(self.okta_client.list_users)

            if error is not None:
                raise Exception(error)
            assert users is not None and resp is not None

            while resp.has_next():
                more_users, _ = await resp.next()
                users.extend(more_users)
            return list(map(lambda user: User(user), users))

        return asyncio.run(_list_users())

    def create_group(self, name: str, description: str) -> Group:
        group, _, error = asyncio.run(
            OktaService._retry(
                self.okta_client.create_group,
                OktaGroupType({"profile": {"name": name, "description": description}}))
        )
        if error is not None:
            raise Exception(error)
        assert group is not None

        return Group(group)

    def update_group(self, groupId: str, name: str, description: str) -> Group:
        group, _, error = asyncio.run(
            OktaService._retry(
                self.okta_client.update_group,
                groupId,
                OktaGroupType({"profile": {"name": name, "description": description}}),
            )
        )
        if error is not None:
            raise Exception(error)
        assert group is not None

        return Group(group)

    async def async_add_user_to_group(self, groupId: str, userId: str) -> None:
        _, error = await OktaService._retry(self.okta_client.add_user_to_group, groupId, userId)
        if error is not None:
            raise Exception(error)
        return

    def add_user_to_group(self, groupId: str, userId: str) -> None:
        _, error = asyncio.run(OktaService._retry(self.okta_client.add_user_to_group, groupId, userId))
        if error is not None:
            raise Exception(error)
        return

    async def async_remove_user_from_group(self, groupId: str, userId: str) -> None:
        _, error = await OktaService._retry(self.okta_client.remove_user_from_group, groupId, userId)
        if error is not None:
            raise Exception(error)
        return

    def remove_user_from_group(self, groupId: str, userId: str) -> None:
        _, error = asyncio.run(OktaService._retry(self.okta_client.remove_user_from_group, groupId, userId))

        if error is not None:
            raise Exception(error)

        return

    # TODO: Implement fetching group membership count for fast syncing
    # GET https://{yourOktaDomain}.com/api/v1/groups/<group_id>?expand=app,stats
    def get_group(self, groupId: str) -> Group:
        group, _, error = asyncio.run(OktaService._retry(self.okta_client.get_group, groupId))

        if error is not None:
            raise Exception(error)

        assert group is not None

        return Group(group)

    DEFAULT_QUERY_PARAMS = {'filter': 'type eq "BUILT_IN" or type eq "OKTA_GROUP"'}
    def list_groups(self, *, query_params: dict[str, str] = DEFAULT_QUERY_PARAMS) -> list[Group]:
        async def _list_groups(query_params: dict[str, str]) -> list[Group]:
            groups, resp, error = await OktaService._retry(self.okta_client.list_groups, query_params=query_params)

            if error is not None:
                raise Exception(error)
            assert groups is not None and resp is not None

            while resp.has_next():
                more_groups, _ = await resp.next()
                groups.extend(more_groups)
            return list(map(lambda group: Group(group), groups))

        return asyncio.run(_list_groups(query_params))

    def list_groups_with_active_rules(self) -> dict[str, list[OktaGroupRuleType]]:
        group_rules = self.list_group_rules()
        group_ids_with_group_rules = {} # type: dict[str, list[OktaGroupRuleType]]
        for group_rule in group_rules:
            if group_rule.status == "ACTIVE":
                for id in group_rule.actions.assign_user_to_groups.group_ids:
                    group_ids_with_group_rules.setdefault(id, []).append(group_rule)
        return group_ids_with_group_rules

    def list_group_rules(self, *, query_params: dict[str,str]={}) -> list[OktaGroupRuleType]:
        async def _list_group_rules(query_params: dict[str, str]) -> list[OktaGroupRuleType]:
            group_rules, resp, error = await OktaService._retry(
                self.okta_client.list_group_rules,
                query_params=query_params
            )

            if error is not None:
                raise Exception(error)

            assert group_rules is not None and resp is not None

            while resp.has_next():
                more_group_rules, _ = await resp.next()
                group_rules.extend(more_group_rules)
            return group_rules

        return asyncio.run(_list_group_rules(query_params))

    def list_users_for_group(self, groupId: str) -> list[User]:
        async def _list_users_for_group(groupId: str) -> list[User]:
            users, resp, error = await OktaService._retry(self.okta_client.list_group_users, groupId)

            if error is not None:
                raise Exception(error)
            assert users is not None and resp is not None

            while resp.has_next():
                more_users, _ = await resp.next()
                users.extend(more_users)
            return list(map(lambda user: User(user), users))

        return asyncio.run(_list_users_for_group(groupId))

    async def async_delete_group(self, groupId: str) -> None:
        _, error = await OktaService._retry(self.okta_client.delete_group, groupId)
        if error is not None:
            raise Exception(error)

    def delete_group(self, groupId: str) -> None:
        _, error = asyncio.run(OktaService._retry(self.okta_client.delete_group, groupId))
        if error is not None:
            raise Exception(error)

    # Below are custom API endpoints that are not supported by the Okta Python SDK
    # https://github.com/okta/okta-sdk-python#call-other-api-endpoints

    # https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/#tag/Group/operation/assignGroupOwner
    def add_owner_to_group(self, groupId: str, userId: str) -> None:
        asyncio.run(self.async_add_owner_to_group(groupId, userId))

    async def async_add_owner_to_group(self, groupId: str, userId: str) -> None:
        request_executor = self.okta_client.get_request_executor()

        request, error = await request_executor.create_request(
            method="POST",
            url="/api/v1/groups/{groupId}/owners".format(groupId=groupId),
            body={"id": userId, "type": "USER"},
            headers={},
            oauth=False,
        )

        if error is not None:
            raise Exception(error)

        _, error = await OktaService._retry(request_executor.execute, request)

        # Ignore error if owner is already assigned to group
        if error is not None and not error.message.endswith(
            "Provided owner is already assigned to this group"
        ):
            raise Exception(error)

        return

    # https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/#tag/Group/operation/deleteGroupOwner
    def remove_owner_from_group(self, groupId: str, userId: str) -> None:
        asyncio.run(self.async_remove_owner_from_group(groupId, userId))

    async def async_remove_owner_from_group(self, groupId: str, userId: str) -> None:
        request_executor = self.okta_client.get_request_executor()

        request, error = await request_executor.create_request(
            method="DELETE",
            url="/api/v1/groups/{groupId}/owners/{userId}".format(
                groupId=groupId, userId=userId
            ),
            body={},
            headers={},
            oauth=False,
        )

        if error is not None:
            raise Exception(error)

        _, error = await OktaService._retry(request_executor.execute, request)

        if error is not None:
            raise Exception(error)

        return

    # https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/#tag/Group/operation/listGroupOwners
    def list_owners_for_group(self, groupId: str) -> list[User]:
        async def _list_owners_for_group(groupId: str) -> list[User]:
            request_executor = self.okta_client.get_request_executor()

            request, error = await request_executor.create_request(
                method="GET",
                url="/api/v1/groups/{groupId}/owners".format(groupId=groupId),
                body={},
                headers={},
                oauth=False,
            )

            if error is not None:
                raise Exception(error)

            response, error = await OktaService._retry(request_executor.execute, request, OktaUserType)

            if error is not None:
                raise Exception(error)
            assert response is not None

            result = []
            for user in response.get_body():
                result.append(User(OktaUserType(user)))
            return result

        return asyncio.run(_list_owners_for_group(groupId))


# Wrapper class for the Okta API user model
class User:
    def __init__(self, user: OktaUserType):
        self.user = user

    def __getattr__(self, name: str) -> Any:
        return getattr(self.user, name)

    def update_okta_user(self, okta_user: OktaUser, user_attrs_to_titles: dict[str, str]) -> OktaUser:
        if okta_user.id is None:
            okta_user.id = self.user.id
        if okta_user.created_at is None:
            okta_user.created_at = dateutil.parser.isoparse(self.user.created)
        if okta_user.updated_at is None:
            okta_user.updated_at = (
                dateutil.parser.isoparse(self.user.last_updated)
                if self.user.last_updated is not None
                else None
            )
        okta_user.deleted_at = self.get_deleted_at()
        okta_user.email = self.user.profile.login
        okta_user.first_name = self.user.profile.first_name
        okta_user.last_name = self.user.profile.last_name
        okta_user.display_name = self.user.profile.display_name
        okta_user.profile = self._convert_profile_keys_to_titles(user_attrs_to_titles)
        okta_user.employee_number = self.user.profile.employee_number
        return okta_user

    def _convert_profile_keys_to_titles(self, user_attrs_to_titles: dict[str, str]) -> dict[str, str]:
        return dict(
            (
                (user_attrs_to_titles.get(k, k), v)
                for (k, v) in self.user.profile.__dict__.items()
            )
        )

    def get_deleted_at(self) -> Optional[datetime.datetime]:
        return (
            dateutil.parser.isoparse(self.user.status_changed)
            if self.user.status in ("SUSPENDED", "DEPROVISIONED")
            else None
        )


# Wrapper class for the Okta API user schema model
class UserSchema:
    def __init__(self, schema: OktaUserSchemaType):
        self.schema = schema

    def __getattr__(self, name: str) -> Any:
        return getattr(self.schema, name)

    @staticmethod
    def _is_camel_case(s: str) -> bool:
        return s != s.lower() and s != s.upper() and "_" not in s

    @staticmethod
    def _to_camel_case(snake_str: str) -> str:
        if UserSchema._is_camel_case(snake_str):
            return snake_str
        tokens = snake_str.split("_")
        return tokens[0].lower() + "".join(x.title() for x in tokens[1:])

    def user_attrs_to_titles(self) -> dict[str, str]:
        user_attributes_to_titles = dict(
            (UserSchema._to_camel_case(k), v.title)
            for (k, v) in self.schema.definitions.base.properties.__dict__.items()
        )
        user_attributes_to_titles.update(
            dict(
                (UserSchema._to_camel_case(k), v["title"])
                for (k, v) in self.schema.definitions.custom.properties.items()
            )
        )
        return user_attributes_to_titles


class Group:
    def __init__(self, group: OktaGroupType):
        self.group = group

    def __getattr__(self, name: str) -> Any:
        return getattr(self.group, name)

    def update_okta_group(
        self,
        okta_group: OktaGroup,
        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]]
    ) -> OktaGroup:
        if okta_group.id is None:
            okta_group.id = self.group.id
        if okta_group.created_at is None:
            okta_group.created_at = dateutil.parser.isoparse(self.group.created)
        if okta_group.updated_at is None:
            okta_group.updated_at = (
                dateutil.parser.isoparse(self.group.last_updated)
                if self.group.last_updated is not None
                else None
            )

        okta_group.name = self.group.profile.name
        okta_group.description = (
            self.group.profile.description
            if self.group.profile.description is not None
            else ""
        )

        okta_group.is_managed = is_managed_group(self, group_ids_with_group_rules)

        # Get externally managed group data
        if self.group.id in group_ids_with_group_rules:
            okta_group.externally_managed_data = {rule.name:rule.conditions.expression.value
                                                  for rule in group_ids_with_group_rules[self.group.id]}


        return okta_group


def is_managed_group(group: Group, group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]]) -> bool:
    return (group.type == "OKTA_GROUP") and (group.id not in group_ids_with_group_rules)
