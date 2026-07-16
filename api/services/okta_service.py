from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from okta.client import Client as OktaClient
from okta.models.add_group_request import AddGroupRequest
from okta.models.assign_group_owner_request_body import AssignGroupOwnerRequestBody
from okta.models.group import Group as OktaGroupType
from okta.models.group_owner_type import GroupOwnerType
from okta.models.group_rule import GroupRule as OktaGroupRuleType
from okta.models.okta_user_group_profile import OktaUserGroupProfile
from okta.models.user import User as OktaUserType
from okta.models.user_schema import UserSchema as OktaUserSchemaType
from okta.models.user_schema_attribute import UserSchemaAttribute
from okta.pagination import PaginationHelper

from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR
from api.models import OktaGroup, OktaUser

REQUEST_TIMEOUT = 30
# HTTP statuses the SDK reports as errors that we treat as transient: a caller
# may swallow them and let the next reconcile retry. Covers rate limiting (429)
# and upstream/gateway blips (5xx, e.g. a 502 Bad Gateway from the load balancer).
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Page size for cursor-paginated list endpoints. Okta caps most list endpoints
# at 200 per page; the facade drives the ``after`` cursor to walk pages.
LIST_PAGE_LIMIT = 200
# Extra attempts for a call that raises ``OktaTransientError``. Transient Okta
# failures (a 429/5xx or timeout, and — via the reclassification in ``_call`` — a
# pooled keep-alive connection Okta dropped server-side, which the SDK surfaces as
# a None-response error) are worth one immediate retry: it gets a fresh connection
# and usually succeeds, recovering within the run instead of skipping the resource
# until the next reconcile. Kept at 1 so a genuinely-degraded endpoint, or a
# lingering 429, still gives up quickly rather than amplifying load.
OKTA_TRANSIENT_RETRIES = 1


logger = logging.getLogger(__name__)


# Okta's user-schema API returns an attribute's ``unique`` property as a string
# enum (``UNIQUE_VALIDATED`` / ``NOT_UNIQUE``) — the default base ``login``,
# ``email``, and ``secondEmail`` attributes are ``UNIQUE_VALIDATED`` — but the
# SDK models the field as a strict bool, so deserializing any user schema raises
# a pydantic ``ValidationError``. Relax the field to match Okta's real payload.
_unique_field = UserSchemaAttribute.model_fields["unique"]
_unique_field.annotation = Optional[str]
_unique_field.metadata = []
UserSchemaAttribute.model_rebuild(force=True)


class OktaTransientError(Exception):
    """Raised when an Okta request times out or hits a transient failure.

    Surfaced when the SDK gives up on a request: its ``requestTimeout`` is
    exceeded, a 429 outlives its rate-limit retries, or the response is a
    transient upstream/gateway error (5xx). Callers that can tolerate a slow or
    flaky Okta call (the membership/ownership fan-out) catch this and continue;
    the syncer reconciles the drift later.
    """

    pass


# On a gateway failure that yields no response object, the SDK returns
# ``(None, None, error)`` from the request executor, then dereferences
# ``response.status`` before checking the error on its list/get endpoints. That
# raises this AttributeError from inside the coroutine instead of returning the
# error in the tuple, so it must be recognised as transient here too.
_NONE_RESPONSE_ATTRIBUTE_ERROR = "'NoneType' object has no attribute 'status'"


def _is_transient_okta_error(error: Any) -> bool:
    """Whether an SDK error is a transient failure a caller may swallow.

    The SDK usually returns (rather than raises) errors. Treat as transient: a request
    timeout — an ``asyncio.TimeoutError`` (aiohttp socket timeout) or the bare
    ``Exception("Request Timeout exceeded.")`` cumulative deadline — and any
    response carrying a transient HTTP ``status`` (429, or a 5xx gateway error).
    Both ``OktaAPIError`` (JSON error bodies) and ``HTTPError`` (non-JSON, e.g. a
    502 HTML page) expose ``.status``. A gateway blip can also leave the SDK with
    a ``None`` response that it dereferences, *raising* an ``AttributeError``; that
    is transient too. ``_call`` routes both returned and raised errors through here.
    """
    if error is None:
        return False
    if isinstance(error, asyncio.TimeoutError):
        return True
    if getattr(error, "status", None) in TRANSIENT_STATUS_CODES:
        return True
    if isinstance(error, AttributeError) and str(error) == _NONE_RESPONSE_ATTRIBUTE_ERROR:
        return True
    return str(error) == "Request Timeout exceeded."


class _WrapperClient:
    """Proxy over an Okta client that routes every async API call through ``OktaService._call``.

    Attribute access returns the underlying client's coroutine methods wrapped
    so their transient failures (timeout, rate limit, 5xx) surface as
    ``OktaTransientError``. Applying the mapping here — rather than at each call
    site — makes it uniform and impossible to forget when a new facade method is
    added. Non-coroutine attributes (e.g. ``get_request_executor``) pass through
    unchanged.
    """

    def __init__(self, client: OktaClient) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        @functools.wraps(attr)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Rebuild the coroutine each attempt — a spent coroutine can't be
            # re-awaited — and retry a bounded number of times on a transient
            # failure, so a stale pooled connection is recovered within the run
            # rather than deferred to the next reconcile.
            for attempt in range(OKTA_TRANSIENT_RETRIES + 1):
                try:
                    return await OktaService._call(attr(*args, **kwargs))
                except OktaTransientError:
                    if attempt == OKTA_TRANSIENT_RETRIES:
                        raise
            raise AssertionError("unreachable")  # pragma: no cover

        return wrapper


class OktaService:
    """For interacting with the Okta API"""

    def __init__(self) -> None:
        self.okta_domain: Optional[str] = None
        self.okta_api_token: Optional[str] = None
        self.use_group_owners_api = False
        # A server-loop-scoped client for connection pooling (see
        # ``start_pooled_client``). ``None`` on CLI/test loops, where each call
        # builds its own client instead.
        self._pooled_client: Optional[OktaClient] = None
        self._pooled_loop: Optional[asyncio.AbstractEventLoop] = None

    def initialize(
        self, okta_domain: Optional[str], okta_api_token: Optional[str], use_group_owners_api: bool = False
    ) -> None:
        # Ignore an okta domain and api token when testing
        if okta_domain is None or okta_api_token is None:
            return
        self.okta_domain = okta_domain
        self.okta_api_token = okta_api_token
        self.use_group_owners_api = use_group_owners_api

    def _build_client(self) -> OktaClient:
        # The SDK owns retries and timeouts: it retries 429s (honoring the
        # X-Rate-Limit-Reset header) up to ``rateLimit.maxRetries`` (default 2),
        # and ``requestTimeout`` bounds each request — an aiohttp per-request
        # timeout plus a cumulative deadline across those retries.
        return OktaClient(
            {
                "orgUrl": f"https://{self.okta_domain}",
                "token": self.okta_api_token,
                "requestTimeout": REQUEST_TIMEOUT,
            }
        )

    @asynccontextmanager
    async def _okta_client(self) -> AsyncIterator[_WrapperClient]:
        """Yield a wrapping proxy over an Okta client bound to the running loop.

        The proxy (``_WrapperClient``) routes every async API call through
        ``_call``. On the FastAPI server loop a single client is created in a
        lifespan hook (``start_pooled_client``) and reused here for connection
        pooling. Every other caller — each CLI command's ``asyncio.run`` in
        ``manage.py`` and each per-test loop under pytest — gets a fresh client
        per call. A client (and its aiohttp session/connector/DNS resolver) must
        never be shared across event loops, which otherwise raises "Future
        attached to a different loop"; the running-loop check below enforces that.
        """
        running_loop = asyncio.get_running_loop()
        if self._pooled_client is not None and self._pooled_loop is running_loop:
            yield _WrapperClient(self._pooled_client)
            return

        client = self._build_client()
        async with client:
            yield _WrapperClient(client)

    async def start_pooled_client(self) -> None:
        """Create a process-wide Okta client bound to the current event loop.

        Called from the FastAPI lifespan so request handlers reuse one client
        (and one pooled aiohttp connector) instead of building a fresh session
        per Okta call. A no-op when Okta is not configured (dev/test), which
        keeps CLI and test loops on the per-call client path.
        """
        if self.okta_domain is None or self.okta_api_token is None:
            return
        client = self._build_client()
        await client.__aenter__()  # type: ignore[no-untyped-call]
        self._pooled_client = client
        self._pooled_loop = asyncio.get_running_loop()

    async def stop_pooled_client(self) -> None:
        """Tear down the pooled client created by ``start_pooled_client``."""
        if self._pooled_client is None:
            return
        client, self._pooled_client = self._pooled_client, None
        self._pooled_loop = None
        await client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]

    @staticmethod
    async def _call(coro: Awaitable[Any]) -> Any:
        """Await an Okta SDK call, mapping transient failures to OktaTransientError.

        The SDK enforces the request timeout itself (``requestTimeout`` config)
        and retries 429s internally, returning errors rather than raising them.
        A transient outcome — a request timeout, a 429 that outlived the SDK's
        rate-limit retries, or a 5xx upstream/gateway error — is surfaced as
        ``OktaTransientError`` so callers can choose to swallow it; every other
        error passes through in the returned tuple for the caller to raise. A
        gateway blip can also make the SDK *raise* (rather than return) a transient
        failure, so raised exceptions are routed through the same check.
        """
        try:
            result = await coro
        except Exception as exc:
            if _is_transient_okta_error(exc):
                raise OktaTransientError(str(exc) or "Okta request timed out") from exc
            raise
        error = result[-1] if isinstance(result, tuple) and result else None
        if _is_transient_okta_error(error):
            raise OktaTransientError(str(error) or "Okta request timed out")
        return result

    async def _paginate(self, list_method: Callable[..., Any], *args: Any, **kwargs: Any) -> list[Any]:
        """Drain a cursor-paginated Okta list endpoint into a single list.

        Each call returns one page plus an ``ApiResponse`` whose ``Link`` header
        carries the ``after`` cursor for the next page. ``*args`` carries any
        positional argument the endpoint requires (e.g. the group id for group
        listings).
        """
        results: list[Any] = []
        after: Optional[str] = None
        while True:
            # ``list_method`` is a proxied client method, so it already routes
            # through ``_call``.
            result = await list_method(*args, limit=LIST_PAGE_LIMIT, after=after, **kwargs)
            # List endpoints are data-returning, so the tuple is (data, resp, error).
            page, response, error = result
            if error is not None:
                raise Exception(error)
            assert page is not None and response is not None
            results.extend(page)
            after = PaginationHelper.extract_next_cursor(response.headers)
            if not after:
                break
        return results

    async def get_user(self, userId: str) -> User:
        async with self._okta_client() as client:
            user, _, error = await client.get_user(userId)

        if error is not None:
            raise Exception(error)

        assert user is not None

        return User(user)

    async def get_user_schema(self, userTypeId: str) -> UserSchema:
        async with self._okta_client() as client:
            userType, user_type_resp, error = await client.get_user_type(userTypeId)

            if error is not None:
                raise Exception(error)

            assert userType is not None and user_type_resp is not None

            # The ``UserType`` model has no ``_links`` field, so recover the
            # schema href from the raw response body instead of the parsed model.
            user_type_body = json.loads(user_type_resp.raw_data)
            schemaId = user_type_body["_links"]["schema"]["href"].rsplit("/", 1).pop()

            schema, _, error = await client.get_user_schema(schemaId)

        if error is not None:
            raise Exception(error)

        return UserSchema(schema)

    async def list_users(self) -> list[User]:
        async with self._okta_client() as client:
            users = await self._paginate(client.list_users)

        return list(map(lambda user: User(user), users))

    async def create_group(self, name: str, description: str) -> Group:
        async with self._okta_client() as client:
            group, _, error = await client.add_group(
                AddGroupRequest(profile=OktaUserGroupProfile(name=name, description=description))
            )

        if error is not None:
            raise Exception(error)

        assert group is not None

        return Group(group)

    async def update_group(self, groupId: str, name: str, description: str) -> Group:
        async with self._okta_client() as client:
            # Fetch Existing Group Data
            existing_group_data, _, get_error = await client.get_group(groupId)
            if get_error is not None:
                logger.error(f"Failed to fetch existing group {groupId} before update: {get_error}")
                raise Exception(f"Failed to fetch existing group {groupId} before update: {get_error}")
            if existing_group_data is None:
                logger.error(f"Group {groupId} not found in Okta before update.")
                raise Exception(f"Group {groupId} not found in Okta before update.")

            # Extract Existing Profile. ``Group.profile`` is an anyOf union
            # wrapper; ``actual_instance.to_dict()`` flattens the standard
            # fields together with any custom profile attributes.
            existing_profile = {}
            existing_actual_profile = getattr(existing_group_data.profile, "actual_instance", None)
            if existing_actual_profile is not None:
                existing_profile = {k: v for k, v in existing_actual_profile.to_dict().items() if v is not None}

            # Merge Updated Profile Data
            new_profile = {**existing_profile}  # Start with a copy of the existing profile
            new_profile["name"] = name  # Update/set the name
            new_profile["description"] = (
                description if description is not None else ""
            )  # Update/set the description (handle None)

            # Construct the New Payload. Custom attributes ride along in the
            # profile dict and land in ``additional_properties``.
            group_payload = AddGroupRequest(profile=OktaUserGroupProfile.from_dict(new_profile))

            # Modify the Update Call to use the new payload
            group, _, error = await client.replace_group(groupId, group_payload)

        if error is not None:
            raise Exception(error)

        assert group is not None

        return Group(group)

    async def add_user_to_group(self, groupId: str, userId: str) -> None:
        if groupId is None or groupId == "":
            logger.warning(f"cannot add user to groupId of {groupId}")
            return

        if userId is None or userId == "":
            logger.warning(f"cannot add user with userId of {userId}")
            return

        try:
            async with self._okta_client() as client:
                *_, error = await client.assign_user_to_group(groupId, userId)

            if error is not None:
                raise Exception(error)
        except OktaTransientError:
            logger.warning(f"Transient Okta error adding user {userId} to group {groupId}")

    async def remove_user_from_group(self, groupId: str, userId: str) -> None:
        if groupId is None or groupId == "":
            logger.warning(f"cannot remove user from groupId of {groupId}")
            return

        if userId is None or userId == "":
            logger.warning(f"cannot remove user with userId of {userId}")
            return

        try:
            async with self._okta_client() as client:
                *_, error = await client.unassign_user_from_group(groupId, userId)

            if error is not None:
                raise Exception(error)
        except OktaTransientError:
            logger.warning(f"Transient Okta error removing user {userId} from group {groupId}")

    # TODO: Implement fetching group membership count for fast syncing
    # GET https://{yourOktaDomain}.com/api/v1/groups/<group_id>?expand=app,stats
    async def get_group(self, groupId: str) -> Group:
        async with self._okta_client() as client:
            group, _, error = await client.get_group(groupId)

        if error is not None:
            raise Exception(error)

        assert group is not None

        return Group(group)

    DEFAULT_QUERY_PARAMS = {"filter": 'type eq "BUILT_IN" or type eq "OKTA_GROUP"'}

    async def list_groups(self, *, query_params: dict[str, str] = DEFAULT_QUERY_PARAMS) -> list[Group]:
        # ``query_params`` keys (``filter``, ``q``, ``search``) map directly onto
        # the ``list_groups`` keyword arguments.
        async with self._okta_client() as client:
            groups = await self._paginate(client.list_groups, **query_params)

        return list(map(lambda group: Group(group), groups))

    async def list_groups_with_active_rules(self) -> dict[str, list[OktaGroupRuleType]]:
        group_rules = await self.list_group_rules()
        group_ids_with_group_rules = {}  # type: dict[str, list[OktaGroupRuleType]]
        for group_rule in group_rules:
            # The SDK types the rule graph as deeply Optional; an active rule
            # always carries its assignment target group ids.
            rule: Any = group_rule
            if rule.status == "ACTIVE":
                for id in rule.actions.assign_user_to_groups.group_ids:
                    group_ids_with_group_rules.setdefault(id, []).append(group_rule)
        return group_ids_with_group_rules

    async def list_group_rules(self, *, query_params: dict[str, str] = {}) -> list[OktaGroupRuleType]:
        async with self._okta_client() as client:
            group_rules = await self._paginate(client.list_group_rules, **query_params)

        return group_rules

    async def list_users_for_group(self, groupId: str) -> list[User]:
        async with self._okta_client() as client:
            users = await self._paginate(client.list_group_users, groupId)

        return list(map(lambda user: User(user), users))

    async def delete_group(self, groupId: str) -> None:
        async with self._okta_client() as client:
            *_, error = await client.delete_group(groupId)

        if error is not None:
            raise Exception(error)

    # https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/#tag/Group/operation/assignGroupOwner
    async def add_owner_to_group(self, groupId: str, userId: str) -> None:
        if groupId is None or groupId == "":
            logger.warning(f"cannot add owner to groupId of {groupId}")
            return
        if userId is None or userId == "":
            logger.warning(f"cannot add owner to userId of {userId}")
            return
        if not self.use_group_owners_api:
            return

        try:
            async with self._okta_client() as client:
                *_, error = await client.assign_group_owner(
                    groupId, AssignGroupOwnerRequestBody(id=userId, type=GroupOwnerType.USER)
                )

            # Ignore error if owner is already assigned to group
            if error is not None and "already assigned to this group" not in str(error):
                raise Exception(error)
        except OktaTransientError:
            logger.warning(f"Transient Okta error adding owner {userId} to group {groupId}")

        return

    # https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/#tag/Group/operation/deleteGroupOwner
    async def remove_owner_from_group(self, groupId: str, userId: str) -> None:
        if groupId is None or groupId == "":
            logger.warning(f"cannot to remove owner from groupId of {groupId}")
            return
        if userId is None or userId == "":
            logger.warning(f"cannot remove owner from userId of {userId}")
            return
        if not self.use_group_owners_api:
            return

        try:
            async with self._okta_client() as client:
                # A group owner of type USER is identified by the user's id.
                *_, error = await client.delete_group_owner(groupId, userId)

            if error is not None:
                raise Exception(error)
        except OktaTransientError:
            logger.warning(f"Transient Okta error removing owner {userId} from group {groupId}")

        return

    # https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/#tag/Group/operation/listGroupOwners
    async def list_owners_for_group(self, groupId: str) -> list[User]:
        if groupId is None or groupId == "":
            logger.warning(f"cannot to list owners for groupId of {groupId}")
            return []
        if not self.use_group_owners_api:
            return []

        async with self._okta_client() as client:
            owners = await self._paginate(client.list_group_owners, groupId)

        # ``GroupOwner`` exposes the owning user's ``id``; wrap it in the same
        # loose accessor other okta reads return so callers stay uniform.
        return list(map(lambda owner: User(owner), owners))


# Wrapper class for the Okta API user model
class User:
    def __init__(self, user: OktaUserType):
        self.user = user

    def __getattr__(self, name: str) -> Any:
        return getattr(self.user, name)

    def update_okta_user(self, okta_user: OktaUser, user_attrs_to_titles: dict[str, str]) -> OktaUser:
        # The SDK types every user/profile field as Optional; this bridge reads
        # them loosely.
        user: Any = self.user
        if okta_user.id is None:
            okta_user.id = user.id
        if okta_user.created_at is None:
            okta_user.created_at = user.created
        if okta_user.updated_at is None:
            okta_user.updated_at = user.last_updated
        okta_user.deleted_at = self.get_deleted_at()
        okta_user.email = user.profile.login
        okta_user.first_name = user.profile.first_name
        okta_user.last_name = user.profile.last_name
        okta_user.display_name = user.profile.display_name
        okta_user.profile = self._convert_profile_keys_to_titles(user_attrs_to_titles)
        okta_user.employee_number = user.profile.employee_number
        return okta_user

    def _convert_profile_keys_to_titles(self, user_attrs_to_titles: dict[str, str]) -> dict[str, str]:
        # ``UserProfile`` is a Pydantic model. Dump the standard fields under
        # their camelCase aliases (to match the schema title map) and fold in
        # any custom attributes carried in ``additional_properties``.
        profile: Any = self.user.profile
        profile_data = profile.model_dump(by_alias=True, exclude={"additional_properties"})
        profile_data.update(profile.additional_properties or {})
        return dict(((user_attrs_to_titles.get(k, k), v) for (k, v) in profile_data.items()))

    def get_deleted_at(self) -> Optional[datetime]:
        user: Any = self.user
        return user.status_changed if user.status in ("SUSPENDED", "DEPROVISIONED") else None


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
        # Base properties are a typed model whose fields (snake_case) are each an
        # optional ``UserSchemaAttribute``; custom properties are a plain dict of
        # ``UserSchemaAttribute`` keyed by attribute name.
        user_attributes_to_titles = {}
        definitions: Any = self.schema.definitions
        base_properties = definitions.base.properties
        for k, attr in vars(base_properties).items():
            if attr is None:
                continue
            user_attributes_to_titles[UserSchema._to_camel_case(k)] = attr.title
        custom_properties = definitions.custom.properties or {}
        for k, attr in custom_properties.items():
            user_attributes_to_titles[UserSchema._to_camel_case(k)] = attr.title
        return user_attributes_to_titles


class Group:
    def __init__(self, group: OktaGroupType):
        self.group = group

    def __getattr__(self, name: str) -> Any:
        return getattr(self.group, name)

    def update_okta_group(
        self, okta_group: OktaGroup, group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]]
    ) -> OktaGroup:
        if okta_group.id is None:
            okta_group.id = self.group.id
        if okta_group.created_at is None:
            okta_group.created_at = self.group.created
        if okta_group.updated_at is None:
            okta_group.updated_at = self.group.last_updated

        profile: Any = _group_profile(self.group)
        okta_group.name = profile.name
        okta_group.description = profile.description if profile.description is not None else ""

        okta_group.is_managed = is_managed_group(self, group_ids_with_group_rules)

        # Get externally managed group data
        if self.group.id in group_ids_with_group_rules:
            rules: list[Any] = group_ids_with_group_rules[self.group.id]
            okta_group.externally_managed_data = {rule.name: rule.conditions.expression.value for rule in rules}

        return okta_group


def _group_profile(group: Any) -> Any:
    """Return the concrete group profile behind the ``GroupProfile`` anyOf union.

    ``Group.profile`` is a union wrapper that does not proxy attribute access to
    its member, so callers must reach through ``actual_instance`` to read
    ``name``/``description``/custom attributes.
    """
    profile = getattr(group, "profile", None)
    if profile is None:
        return None
    return getattr(profile, "actual_instance", profile)


def is_managed_group(
    group: Group,
    group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]],
    custom_attr: Optional[str] = OKTA_GROUP_PROFILE_CUSTOM_ATTR,
) -> bool:
    # Check if OKTA_GROUP_PROFILE_CUSTOM_ATTR attribute exists as a custom Okta Group Profile attribute and retrieve its value
    if custom_attr:
        profile = _group_profile(group)
        # Custom attributes live in the profile's ``additional_properties`` (or
        # flattened via ``to_dict``); a plain ``getattr`` on the model won't see them.
        custom_manage_attr = None
        if profile is not None and hasattr(profile, "to_dict"):
            custom_manage_attr = profile.to_dict().get(custom_attr)
        elif profile is not None:
            custom_manage_attr = getattr(profile, custom_attr, None)

        # If OKTA_GROUP_PROFILE_CUSTOM_ATTR is explicitly set to False, the group should not be managed
        if custom_manage_attr is False:
            return False

        # If OKTA_GROUP_PROFILE_CUSTOM_ATTR is True and the group type is OKTA_GROUP, it can be managed even if it has group rules
        if custom_manage_attr is True and group.type == "OKTA_GROUP":
            return True

    # By default, the group should be of type OKTA_GROUP and should not have any group rules to be managed
    return (group.type == "OKTA_GROUP") and (group.id not in group_ids_with_group_rules)
