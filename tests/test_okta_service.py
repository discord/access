import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from okta.exceptions.exceptions import ApiException
from okta.models.add_group_request import AddGroupRequest
from okta.models.group import Group as OktaGroupType
from okta.models.group_push_mapping import GroupPushMapping
from okta.models.group_rule import GroupRule as OktaGroupRuleType
from okta.models.user_schema import UserSchema as OktaUserSchemaType

from api.services.okta_service import (
    OKTA_TRANSIENT_RETRIES,
    OktaService,
    OktaTransientError,
    UserSchema,
    is_managed_group,
)
from tests.factories import UserFactory


def _user_schema(base_properties: dict[str, Any], custom_properties: Optional[dict[str, Any]] = None) -> Any:
    """Build a real Okta ``UserSchema`` model from a raw schema payload."""
    schema = OktaUserSchemaType.from_dict(
        {
            "definitions": {
                "base": {"id": "#base", "type": "object", "properties": base_properties},
                "custom": {"id": "#custom", "type": "object", "properties": custom_properties or {}},
            }
        }
    )
    assert schema is not None
    return schema


def _okta_group(allow_discord_access: Any = "unset") -> OktaGroupType:
    """Build a real Okta Group whose profile optionally carries the custom
    ``allow_discord_access`` attribute (which lives in the profile's
    additional_properties)."""
    profile: dict[str, Any] = {"name": "n", "description": "d"}
    if allow_discord_access != "unset":
        profile["allow_discord_access"] = allow_discord_access
    group = OktaGroupType.from_dict({"id": "123456789", "type": "OKTA_GROUP", "profile": profile})
    assert group is not None
    return group


def test_is_managed_group_with_allow_discord_access_false() -> None:
    """Test that is_managed_group returns False when allow_discord_access is False."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        group = _okta_group(allow_discord_access=False)
        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is False


def test_is_managed_group_with_allow_discord_access_true() -> None:
    """Test that is_managed_group returns True when allow_discord_access is True."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        group = _okta_group(allow_discord_access=True)
        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


def test_is_managed_group_with_allow_discord_access_undefined() -> None:
    """Test that is_managed_group returns True when the custom attribute is undefined."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", None):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        group = _okta_group(allow_discord_access=False)
        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


async def test_update_group_preserves_custom_attributes() -> None:
    """Test that update_group preserves custom attributes when updating a group."""
    service = OktaService()

    group_id = "test-group-id"

    # An existing group whose profile carries a custom attribute
    # (``allow_discord_access`` lives in the profile's additional_properties).
    existing_group = OktaGroupType.from_dict(
        {
            "id": group_id,
            "type": "OKTA_GROUP",
            "profile": {"name": "Old Name", "description": "Old Description", "allow_discord_access": True},
        }
    )

    # Mock the per-call Okta client's get_group and replace_group methods
    mock_client = MagicMock()
    mock_client.get_group = AsyncMock(return_value=(existing_group, None, None))
    mock_client.replace_group = AsyncMock(return_value=(existing_group, None, None))

    # Mock the _okta_client async context manager to yield the mock client
    class MockOktaClientContext:
        """Async context manager that yields the mock Okta client"""

        async def __aenter__(self) -> MagicMock:
            return mock_client

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

    # Use patch to mock the _okta_client method
    # This avoids directly assigning to the method, which mypy doesn't like
    with patch.object(service, "_okta_client", return_value=MockOktaClientContext()):
        # Call update_group
        await service.update_group(group_id, "New Name", "New Description")

    # Verify replace_group was called with a payload that preserved the custom attribute
    args, _ = mock_client.replace_group.call_args
    assert len(args) == 2
    assert args[0] == group_id

    # Check that the payload contains both the updated fields and the preserved custom attribute
    updated_payload = args[1]
    assert isinstance(updated_payload, AddGroupRequest)
    assert updated_payload.profile.name == "New Name"
    assert updated_payload.profile.description == "New Description"
    assert updated_payload.profile.to_dict()["allow_discord_access"] is True


def test_user_schema_parses_okta_string_unique_enum() -> None:
    """Okta returns a schema attribute's ``unique`` property as a string enum
    (``UNIQUE_VALIDATED`` / ``NOT_UNIQUE``) — the default base ``login`` and
    ``email`` attributes are ``UNIQUE_VALIDATED`` — but the okta SDK models the
    field as a strict bool. Parsing such a schema must not raise, and titles
    must remain extractable for base and custom attributes alike."""
    schema = _user_schema(
        {
            "login": {"title": "Login", "unique": "UNIQUE_VALIDATED"},
            "email": {"title": "Email", "unique": "UNIQUE_VALIDATED"},
            "firstName": {"title": "First Name", "unique": "NOT_UNIQUE"},
        },
        {"employeeNumber": {"title": "Employee Number", "unique": "UNIQUE_VALIDATED"}},
    )

    titles = UserSchema(schema).user_attrs_to_titles()

    assert titles["login"] == "Login"
    assert titles["email"] == "Email"
    assert titles["firstName"] == "First Name"
    assert titles["employeeNumber"] == "Employee Number"


async def test_get_user_schema_resolves_schema_with_unique_attributes() -> None:
    """End-to-end: ``get_user_schema`` recovers the schema href from the user
    type and returns a usable schema even when attributes carry Okta's string
    ``unique`` enum (the shape that crashed the sync cron under okta 3.4.4)."""
    service = OktaService()

    user_type_resp = MagicMock()
    user_type_resp.raw_data = json.dumps(
        {"_links": {"schema": {"href": "https://example.okta.com/api/v1/meta/schemas/user/osc123"}}}
    )
    schema_model = _user_schema({"login": {"title": "Login", "unique": "UNIQUE_VALIDATED"}})

    mock_client = MagicMock()
    mock_client.get_user_type = AsyncMock(return_value=(MagicMock(), user_type_resp, None))
    mock_client.get_user_schema = AsyncMock(return_value=(schema_model, None, None))

    class MockOktaClientContext:
        async def __aenter__(self) -> MagicMock:
            return mock_client

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

    with patch.object(service, "_okta_client", return_value=MockOktaClientContext()):
        result = await service.get_user_schema("default")

    assert result.user_attrs_to_titles()["login"] == "Login"
    mock_client.get_user_schema.assert_awaited_once_with("osc123")


async def test_concurrent_calls_use_isolated_clients() -> None:
    """Concurrent Okta calls must each get their own client (and aiohttp session).

    Off the server loop, ``_okta_client()`` builds a fresh SDK client per call
    so nothing is shared across concurrent calls. A previous design shared one
    client (and its request executor/session) across all calls, so concurrent
    session setup/teardown could race. This asserts the per-call isolation
    directly: N concurrent calls build N distinct clients.
    """
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    clients: list[Any] = []
    real_build_client = service._build_client

    def tracking_build_client() -> Any:
        client = real_build_client()
        clients.append(client)
        return client

    call_count = 16
    success = (UserFactory(), MagicMock(status_code=200, headers={}), None)

    with (
        patch.object(service, "_build_client", side_effect=tracking_build_client),
        patch("okta.client.Client.get_user", return_value=success),
    ):
        users = await asyncio.gather(*(service.get_user("okta_id") for _ in range(call_count)))

    # No call raised (gather re-raises), and every call returned a user.
    assert len(users) == call_count
    assert all(user is not None for user in users)
    # Each concurrent call built its own client; nothing shared.
    assert len(clients) == call_count
    assert len({id(client) for client in clients}) == call_count


async def test_connection_level_error_is_retried_then_succeeds() -> None:
    """A dropped pooled connection is retried within the same call and recovers.

    A stale keep-alive connection Okta closed server-side surfaces as the SDK's
    None-response ``AttributeError``; ``_call`` maps it to a *retryable*
    ``OktaTransientError``, ``_WrapperClient`` retries it, and the next attempt —
    on a fresh connection in production — succeeds.
    """
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    dropped = AttributeError("'NoneType' object has no attribute 'status'")
    success = (UserFactory(), MagicMock(status_code=200, headers={}), None)
    get_user = AsyncMock(side_effect=[dropped, success])

    with patch("okta.client.Client.get_user", get_user):
        user = await service.get_user("okta_id")

    assert user is not None
    # One connection-level failure, retried once, then success.
    assert get_user.call_count == 2


async def test_connection_level_error_gives_up_after_bounded_retries() -> None:
    """A persistently dropped connection stops after the bounded retries."""
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    get_user = AsyncMock(side_effect=AttributeError("'NoneType' object has no attribute 'status'"))

    with patch("okta.client.Client.get_user", get_user):
        with pytest.raises(OktaTransientError):
            await service.get_user("okta_id")

    assert get_user.call_count == OKTA_TRANSIENT_RETRIES + 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_definitive_transient_status_is_not_retried(status: int) -> None:
    """A 429 or 5xx is transient but *not* retried — the SDK already spent its
    rate-limit/timeout budget, so re-issuing into an active storm would only
    amplify load. It surfaces as ``OktaTransientError`` on the first attempt."""
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    transient = (None, MagicMock(status_code=status, headers={}), MagicMock(status=status))
    get_user = AsyncMock(return_value=transient)

    with patch("okta.client.Client.get_user", get_user):
        with pytest.raises(OktaTransientError):
            await service.get_user("okta_id")

    assert get_user.call_count == 1


async def test_request_timeout_deadline_is_not_retried() -> None:
    """An exhausted request-timeout deadline is transient but not retried."""
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    get_user = AsyncMock(return_value=(None, None, Exception("Request Timeout exceeded.")))

    with patch("okta.client.Client.get_user", get_user):
        with pytest.raises(OktaTransientError):
            await service.get_user("okta_id")

    assert get_user.call_count == 1


def _fake_okta_client(mocker, svc):
    """Wire svc._okta_client() to yield a MagicMock client. Returns the client so tests
    can set its high-level group-push methods' return values / side effects.

    The methods now go through the SDK's typed ``GroupPushMappingApi`` (inherited by the
    Okta ``Client``), which returns ``(data, ApiResponse, error)`` tuples and raises
    ``ApiException`` on HTTP errors — so tests script those methods directly rather than
    the low-level request executor."""
    client = MagicMock()

    @asynccontextmanager
    async def fake_client():
        yield client

    mocker.patch.object(svc, "_okta_client", fake_client)
    return client


def _mapping(**fields):
    """Build a real ``GroupPushMapping`` model from camelCase fields, as the SDK returns."""
    mapping = GroupPushMapping.from_dict(fields)
    assert mapping is not None
    return mapping


def _list_result(data, *, next_cursor=None):
    """A ``(data, ApiResponse, error)`` tuple; sets a Link header when a next page exists."""
    resp = MagicMock()
    resp.headers = (
        {"Link": f'<https://x/api/v1/apps/app-1/group-push/mappings?after={next_cursor}>; rel="next"'}
        if next_cursor
        else {}
    )
    return (data, resp, None)


async def test_create_group_push_mapping_posts_active_mapping(mocker):
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    created = _mapping(id="map-123", sourceGroupId="src-1", targetGroupId="tgt-1", status="ACTIVE")
    client.create_group_push_mapping = AsyncMock(return_value=(created, MagicMock(), None))

    result = await svc.create_group_push_mapping("app-1", "src-1", "tgt-1")

    assert result == {"id": "map-123", "sourceGroupId": "src-1", "targetGroupId": "tgt-1", "status": "ACTIVE"}
    args, _ = client.create_group_push_mapping.call_args
    assert args[0] == "app-1"
    body = args[1].model_dump(by_alias=True, mode="json", exclude_none=True)
    assert body == {"sourceGroupId": "src-1", "targetGroupId": "tgt-1", "status": "ACTIVE"}


async def test_create_group_push_mapping_posts_new_group_by_name(mocker):
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    created = _mapping(id="map-123", sourceGroupId="src-1", status="ACTIVE")
    client.create_group_push_mapping = AsyncMock(return_value=(created, MagicMock(), None))

    result = await svc.create_group_push_mapping("app-1", "src-1", targetGroupName="gcp-security")

    assert result == {"id": "map-123", "sourceGroupId": "src-1", "status": "ACTIVE"}
    args, _ = client.create_group_push_mapping.call_args
    body = args[1].model_dump(by_alias=True, mode="json", exclude_none=True)
    assert body == {"sourceGroupId": "src-1", "targetGroupName": "gcp-security", "status": "ACTIVE"}


async def test_create_group_push_mapping_parses_raw_body_when_untyped(mocker):
    # Okta returns HTTP 200 for create-or-link, but the SDK only types the spec'd 201, so it
    # returns data=None with the JSON in response.raw_data. The method must parse that body.
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    resp = MagicMock()
    resp.raw_data = b'{"id":"map-200","sourceGroupId":"src-1","targetGroupId":"tgt-1","status":"ACTIVE"}'
    client.create_group_push_mapping = AsyncMock(return_value=(None, resp, None))

    result = await svc.create_group_push_mapping("app-1", "src-1", "tgt-1")

    assert result == {"id": "map-200", "sourceGroupId": "src-1", "targetGroupId": "tgt-1", "status": "ACTIVE"}


@pytest.mark.parametrize("args", [("", "src", "tgt"), ("app", "", "tgt"), ("app", "src", "")])
async def test_create_group_push_mapping_requires_args(args):
    svc = OktaService()
    with pytest.raises(ValueError):
        await svc.create_group_push_mapping(*args)


async def test_create_group_push_mapping_requires_exactly_one_target(mocker):
    svc = OktaService()
    # Neither target.
    with pytest.raises(ValueError, match="exactly one"):
        await svc.create_group_push_mapping("app-1", "src-1")
    # Both targets.
    with pytest.raises(ValueError, match="exactly one"):
        await svc.create_group_push_mapping("app-1", "src-1", targetGroupId="tgt-1", targetGroupName="gcp-security")


async def test_delete_group_push_mapping_deactivates_then_deletes(mocker):
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    client.update_group_push_mapping = AsyncMock(return_value=(_mapping(id="map-1"), MagicMock(), None))
    client.delete_group_push_mapping = AsyncMock(return_value=(None, MagicMock(), None))

    await svc.delete_group_push_mapping("app-1", "map-1", deleteTargetGroup=True)

    # Deactivate (update -> INACTIVE) must precede delete; Okta rejects deleting an ACTIVE mapping.
    update_args, _ = client.update_group_push_mapping.call_args
    assert update_args[0] == "app-1"
    assert update_args[1] == "map-1"
    assert update_args[2].status == "INACTIVE"
    client.delete_group_push_mapping.assert_awaited_once_with("app-1", "map-1", True)


@pytest.mark.parametrize("args", [("", "map-1"), ("app-1", "")])
async def test_delete_group_push_mapping_requires_args(args):
    svc = OktaService()
    with pytest.raises(ValueError):
        await svc.delete_group_push_mapping(*args)


async def test_delete_group_push_mapping_idempotent_on_404(mocker):
    # A 404 means the mapping is already gone; deletion is idempotent, so the operation must
    # succeed rather than raise (covers retry/replay after a partial failure). The typed client
    # raises ApiException on HTTP errors, so simulate the DELETE raising a 404.
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    client.update_group_push_mapping = AsyncMock(return_value=(_mapping(id="map-1"), MagicMock(), None))
    client.delete_group_push_mapping = AsyncMock(side_effect=ApiException(status=404))

    await svc.delete_group_push_mapping("app-1", "map-1")  # must not raise


async def test_delete_group_push_mapping_raises_on_non_404(mocker):
    # A genuine, non-transient failure (e.g. 403) must still surface, not be swallowed by the
    # 404 tolerance. (5xx is handled separately as a transient error before reaching here.)
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    client.update_group_push_mapping = AsyncMock(return_value=(_mapping(id="map-1"), MagicMock(), None))
    client.delete_group_push_mapping = AsyncMock(side_effect=ApiException(status=403))

    with pytest.raises(ApiException):
        await svc.delete_group_push_mapping("app-1", "map-1")


async def test_list_group_push_mappings_returns_body(mocker):
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    mapping = _mapping(id="m1", sourceGroupId="g1", status="ACTIVE")
    client.list_group_push_mappings = AsyncMock(return_value=_list_result([mapping]))

    result = await svc.list_group_push_mappings("app-1")

    assert result == [{"id": "m1", "sourceGroupId": "g1", "status": "ACTIVE"}]
    args, kwargs = client.list_group_push_mappings.call_args
    assert args[0] == "app-1"
    # No source filter by default -> the SDK omits the sourceGroupId query param.
    assert kwargs["source_group_id"] is None


async def test_list_group_push_mappings_filters_by_source_group_id(mocker):
    # Passing sourceGroupId filters server-side so a caller after one source group's mapping
    # doesn't page the whole app's mappings.
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    mapping = _mapping(id="m1", sourceGroupId="g1", targetGroupId="t1", status="ACTIVE")
    client.list_group_push_mappings = AsyncMock(return_value=_list_result([mapping]))

    result = await svc.list_group_push_mappings("app-1", sourceGroupId="g1")

    assert result == [{"id": "m1", "sourceGroupId": "g1", "targetGroupId": "t1", "status": "ACTIVE"}]
    _, kwargs = client.list_group_push_mappings.call_args
    assert kwargs["source_group_id"] == "g1"


async def test_list_group_push_mappings_follows_pagination(mocker):
    # The Group Push Mappings endpoint is paginated via Link headers; list_group_push_mappings
    # must follow the `next` cursor and return every page, not just the first.
    svc = OktaService()
    client = _fake_okta_client(mocker, svc)
    client.list_group_push_mappings = AsyncMock(
        side_effect=[
            _list_result([_mapping(id="m1")], next_cursor="CURSOR"),
            _list_result([_mapping(id="m2")]),
        ]
    )

    result = await svc.list_group_push_mappings("app-1")

    assert result == [{"id": "m1"}, {"id": "m2"}]
    # Page one is fetched with no cursor; page two with the cursor from the Link header.
    assert client.list_group_push_mappings.await_args_list[0].kwargs["after"] is None
    assert client.list_group_push_mappings.await_args_list[1].kwargs["after"] == "CURSOR"


async def test_list_group_push_mappings_requires_app_id():
    svc = OktaService()
    with pytest.raises(ValueError):
        await svc.list_group_push_mappings("")
