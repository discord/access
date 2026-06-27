import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from okta.models.group_rule import GroupRule as OktaGroupRuleType
from okta.request_executor import RequestExecutor

from api.services.okta_service import OktaService, is_managed_group
from tests.factories import UserFactory


def test_is_managed_group_with_allow_discord_access_false() -> None:
    """Test that is_managed_group returns False when allow_discord_access is False."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = False  # Set the profile attribute to False
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is False


def test_is_managed_group_with_allow_discord_access_true() -> None:
    """Test that is_managed_group returns True when allow_discord_access is True."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", "allow_discord_access"):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = True  # Set the profile attribute to True
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


def test_is_managed_group_with_allow_discord_access_undefined() -> None:
    """Test that is_managed_group returns True when the custom attribute is undefined."""
    with patch("api.config.OKTA_GROUP_PROFILE_CUSTOM_ATTR", None):
        from api.config import OKTA_GROUP_PROFILE_CUSTOM_ATTR

        # Create a mock of the Group class
        group = MagicMock()
        group.profile = MagicMock()
        group.profile.allow_discord_access = False  # Set the profile attribute to False
        group.type = "OKTA_GROUP"
        group.id = "123456789"  # Example group ID

        group_ids_with_group_rules: dict[str, list[OktaGroupRuleType]] = {}  # Empty dictionary for group rules

        # Call the function and assert the expected result
        result = is_managed_group(group, group_ids_with_group_rules, OKTA_GROUP_PROFILE_CUSTOM_ATTR)
        assert result is True


def test_update_group_preserves_custom_attributes() -> None:
    """Test that update_group preserves custom attributes when updating a group."""
    # Create a new event loop for this test
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Mock asyncio.run to use our event loop
    with patch("asyncio.run", side_effect=lambda x: loop.run_until_complete(x)):
        # Create OktaService instance
        service = OktaService()

        # Set up the mocks for the existing group and the update call
        group_id = "test-group-id"

        # Create a mock group with a profile that has the custom attribute
        existing_group = MagicMock()
        # Instead of setting __dict__ directly, configure the mock properly
        existing_group.profile = MagicMock()
        existing_group.profile.name = "Old Name"
        existing_group.profile.description = "Old Description"
        existing_group.profile.allow_discord_access = True

        # Mock the per-call Okta client's get_group and update_group methods
        mock_client = MagicMock()
        mock_client.get_group = AsyncMock(return_value=(existing_group, None, None))
        mock_client.update_group = AsyncMock(return_value=(MagicMock(), None, None))

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
            service.update_group(group_id, "New Name", "New Description")

            # Verify update_group was called with a payload that preserved the custom attribute
            args, _ = mock_client.update_group.call_args
            assert len(args) == 2
            assert args[0] == group_id

            # Check that the payload contains both the updated fields and the preserved custom attribute
            updated_payload = args[1]
            assert updated_payload.profile.name == "New Name"
            assert updated_payload.profile.description == "New Description"
            assert updated_payload.profile.allow_discord_access is True


def test_concurrent_calls_use_isolated_request_executors() -> None:
    """Concurrent Okta calls must each get their own client, executor, and session.

    Every public method runs its coroutine under its own ``asyncio.run`` event
    loop, and the FastAPI/MCP threadpools drive those sync methods concurrently.
    The previous design shared one ``self.okta_client`` (and its request executor)
    across all calls, so concurrent ``set_session()`` calls clobbered each other and
    a session bound to one loop was awaited on another ("Future attached to a
    different loop"). With a fresh client per call, each call binds its session to
    its own executor on its own loop, so nothing is shared to race on.
    """
    service = OktaService()
    service.initialize("fake.domain", "fake.token")

    executors: list[Any] = []
    loops: list[Any] = []
    lock = threading.Lock()

    real_set_session = RequestExecutor.set_session

    def tracking_set_session(executor: Any, session: Any) -> None:
        real_set_session(executor, session)
        with lock:
            executors.append(executor)
            loops.append(asyncio.get_running_loop())

    call_count = 16
    success = (UserFactory(), MagicMock(), None)

    with (
        patch.object(RequestExecutor, "set_session", tracking_set_session),
        patch("okta.client.Client.get_user", return_value=success),
    ):
        with ThreadPoolExecutor(max_workers=8) as pool:
            users = list(pool.map(lambda _: service.get_user("okta_id"), range(call_count)))

    # No call raised (pool.map re-raises), and every call returned a user.
    assert len(users) == call_count
    assert all(user is not None for user in users)
    # Each concurrent call got its own executor on its own event loop; nothing shared.
    assert len({id(executor) for executor in executors}) == call_count
    assert len({id(loop) for loop in loops}) == call_count


def _mock_okta_executor(mocker, svc, *, body=None, retry_side_effect=None):
    """Wire svc._okta_client() to a mock request executor. Returns the executor.

    By default every _retry call resolves to a single successful response; pass
    retry_side_effect to script per-call outcomes (e.g. to simulate an error status)."""
    executor = MagicMock()
    executor.create_request = AsyncMock(return_value=(MagicMock(), None))
    response = MagicMock()
    response.get_body.return_value = body if body is not None else {}
    response.has_next.return_value = False  # single page by default; pagination tested separately
    client = MagicMock()
    client.get_request_executor.return_value = executor

    @asynccontextmanager
    async def fake_client():
        yield client

    mocker.patch.object(svc, "_okta_client", fake_client)
    if retry_side_effect is not None:
        mocker.patch.object(OktaService, "_retry", AsyncMock(side_effect=retry_side_effect))
    else:
        mocker.patch.object(OktaService, "_retry", AsyncMock(return_value=(response, None)))
    return executor


def test_create_group_push_mapping_posts_active_mapping(mocker):
    svc = OktaService()
    executor = _mock_okta_executor(mocker, svc, body={"id": "map-123"})

    result = svc.create_group_push_mapping("app-1", "src-1", "tgt-1")

    assert result == {"id": "map-123"}
    _, kwargs = executor.create_request.call_args
    assert kwargs["method"] == "POST"
    assert kwargs["url"] == "/api/v1/apps/app-1/group-push/mappings"
    assert kwargs["body"] == {"sourceGroupId": "src-1", "targetGroupId": "tgt-1", "status": "ACTIVE"}


@pytest.mark.parametrize("args", [("", "src", "tgt"), ("app", "", "tgt"), ("app", "src", "")])
def test_create_group_push_mapping_requires_args(args):
    svc = OktaService()
    with pytest.raises(ValueError):
        svc.create_group_push_mapping(*args)


def test_delete_group_push_mapping_deactivates_then_deletes(mocker):
    svc = OktaService()
    executor = _mock_okta_executor(mocker, svc)

    svc.delete_group_push_mapping("app-1", "map-1", deleteTargetGroup=True)

    calls = executor.create_request.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["method"] == "PATCH"
    assert calls[0].kwargs["url"] == "/api/v1/apps/app-1/group-push/mappings/map-1"
    assert calls[0].kwargs["body"] == {"status": "INACTIVE"}
    assert calls[1].kwargs["method"] == "DELETE"
    assert calls[1].kwargs["url"] == "/api/v1/apps/app-1/group-push/mappings/map-1?deleteTargetGroup=true"


@pytest.mark.parametrize("args", [("", "map-1"), ("app-1", "")])
def test_delete_group_push_mapping_requires_args(args):
    svc = OktaService()
    with pytest.raises(ValueError):
        svc.delete_group_push_mapping(*args)


def test_delete_group_push_mapping_idempotent_on_404(mocker):
    # A 404 means the mapping is already gone; deletion is idempotent, so the operation must
    # succeed rather than raise (covers retry/replay after a partial failure).
    svc = OktaService()
    not_found = MagicMock(status=404)
    # PATCH (deactivate) succeeds; DELETE returns 404 (already gone).
    _mock_okta_executor(mocker, svc, retry_side_effect=[(MagicMock(), None), (None, not_found)])

    svc.delete_group_push_mapping("app-1", "map-1")  # must not raise


def test_delete_group_push_mapping_raises_on_non_404(mocker):
    # A genuine failure (e.g. 500) must still surface, not be swallowed by the 404 tolerance.
    svc = OktaService()
    server_error = MagicMock(status=500)
    _mock_okta_executor(mocker, svc, retry_side_effect=[(MagicMock(), None), (None, server_error)])

    with pytest.raises(Exception):
        svc.delete_group_push_mapping("app-1", "map-1")


def test_list_group_push_mappings_returns_body(mocker):
    svc = OktaService()
    executor = _mock_okta_executor(mocker, svc, body=[{"id": "m1", "sourceGroupId": "g1"}])

    result = svc.list_group_push_mappings("app-1")

    assert result == [{"id": "m1", "sourceGroupId": "g1"}]
    _, kwargs = executor.create_request.call_args
    assert kwargs["method"] == "GET"
    assert kwargs["url"] == "/api/v1/apps/app-1/group-push/mappings"


def test_list_group_push_mappings_follows_pagination(mocker):
    # The Group Push Mappings endpoint is paginated via Link headers; list_group_push_mappings
    # must follow the `next` link and return every page, not just the first.
    svc = OktaService()
    executor = MagicMock()
    executor.create_request = AsyncMock(return_value=(MagicMock(), None))
    client = MagicMock()
    client.get_request_executor.return_value = executor

    @asynccontextmanager
    async def fake_client():
        yield client

    mocker.patch.object(svc, "_okta_client", fake_client)

    page1 = MagicMock()
    page1.get_body.return_value = [{"id": "m1"}]
    page1.has_next.side_effect = [True, False]  # one further page, then exhausted
    # _retry is invoked first for the initial execute (-> page1 response), then for page1.next
    # (-> the second page's body), matching the low-level OktaAPIResponse pagination contract.
    mocker.patch.object(
        OktaService,
        "_retry",
        AsyncMock(side_effect=[(page1, None), ([{"id": "m2"}], None)]),
    )

    result = svc.list_group_push_mappings("app-1")

    assert result == [{"id": "m1"}, {"id": "m2"}]


def test_list_group_push_mappings_requires_app_id():
    svc = OktaService()
    with pytest.raises(ValueError):
        svc.list_group_push_mappings("")
