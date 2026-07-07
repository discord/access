import asyncio
from typing import Any, Optional, Tuple
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

from api.services.okta_service import REQUEST_MAX_RETRIES, RETRIABLE_STATUS_CODES, OktaService, OktaTimeout
from tests.factories import UserFactory


def mock_get_user(mocker: MockerFixture, status_code: int) -> Mock:
    # The SDK returns an ``ApiResponse``-shaped object; ``_retry`` reads
    # ``.status_code`` and ``.headers`` off it.
    mock_response_object = Mock()
    mock_response_object.status_code = status_code
    mock_response_object.headers = {}
    if status_code < 400:  # success
        mock_response: Tuple[Any, Any, Optional[Exception]] = (UserFactory(), mock_response_object, None)
    else:  # error
        mock_response = (None, mock_response_object, Exception())
    return mocker.patch("okta.client.Client.get_user", return_value=mock_response)


@pytest.fixture
def okta_service() -> OktaService:
    service = OktaService()
    service.initialize("fake.domain", "fake.token")
    return service


@pytest.fixture
def mock_sleep(mocker: MockerFixture) -> Mock:
    return mocker.patch("asyncio.sleep")


@pytest.mark.parametrize("status_code", RETRIABLE_STATUS_CODES)
async def test_retry_logic_error_response_retriable(
    mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService, status_code: int
) -> None:
    mocked_request = mock_get_user(mocker, status_code)

    with pytest.raises(Exception):
        await okta_service.get_user("okta_id")
        assert mocked_request.call_count == 1 + REQUEST_MAX_RETRIES
        assert mock_sleep.call_count == REQUEST_MAX_RETRIES


async def test_retry_logic_error_response_non_retriable(
    mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService
) -> None:
    mocked_request = mock_get_user(mocker, 400)

    with pytest.raises(Exception):
        await okta_service.get_user("okta_id")
        assert mocked_request.call_count == 1
        assert mock_sleep.call_count == 0


async def test_retry_logic_no_error(mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService) -> None:
    mocked_request = mock_get_user(mocker, 200)

    await okta_service.get_user("okta_id")
    assert mocked_request.call_count == 1
    assert mock_sleep.call_count == 0


def _fake_wait_for(outcomes: list[Any]) -> Any:
    """side_effect for a patched asyncio.wait_for that closes the wrapped
    coroutine (the real wait_for would consume it; leaving it unawaited
    trips the `coroutine ... was never awaited` warning-as-error filter)."""

    def fake_wait_for(coro: Any, timeout: float) -> Any:
        coro.close()
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return fake_wait_for


async def test_retry_logic_all_timeouts_raises(
    mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService
) -> None:
    mocker.patch(
        "asyncio.wait_for",
        side_effect=_fake_wait_for([asyncio.TimeoutError() for _ in range(1 + REQUEST_MAX_RETRIES)]),
    )

    with pytest.raises(OktaTimeout, match="timed out"):
        await okta_service.get_user("okta_id")


async def test_retry_logic_timeout_then_success(
    mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService
) -> None:
    success_response: Tuple[Any, Any, Optional[Exception]] = (UserFactory(), Mock(), None)
    mocker.patch(
        "asyncio.wait_for",
        side_effect=_fake_wait_for([asyncio.TimeoutError(), success_response]),
    )

    user = await okta_service.get_user("okta_id")
    assert user is not None
