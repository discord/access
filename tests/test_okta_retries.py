from typing import Any, Optional, Tuple
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

from api.services.okta_service import REQUEST_MAX_RETRIES, RETRIABLE_STATUS_CODES, OktaService
from tests.factories import UserFactory


def mock_get_user(mocker: MockerFixture, status_code: int) -> Mock:
    mock_response_object = Mock()
    mock_response_object.configure_mock(**{"get_status_code.return_value": status_code})
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
def test_retry_logic_error_response_retriable(
    mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService, status_code: int
) -> None:
    mocked_request = mock_get_user(mocker, status_code)

    with pytest.raises(Exception):
        okta_service.get_user("okta_id")
        assert mocked_request.call_count == 1 + REQUEST_MAX_RETRIES
        assert mock_sleep.call_count == REQUEST_MAX_RETRIES


def test_retry_logic_error_response_non_retriable(
    mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService
) -> None:
    mocked_request = mock_get_user(mocker, 400)

    with pytest.raises(Exception):
        okta_service.get_user("okta_id")
        assert mocked_request.call_count == 1
        assert mock_sleep.call_count == 0


def test_retry_logic_no_error(mocker: MockerFixture, mock_sleep: Mock, okta_service: OktaService) -> None:
    mocked_request = mock_get_user(mocker, 200)

    okta_service.get_user("okta_id")
    assert mocked_request.call_count == 1
    assert mock_sleep.call_count == 0
