import asyncio
from unittest.mock import MagicMock

import pytest
from okta.errors.okta_api_error import OktaAPIError
from pytest_mock import MockerFixture

from api.services.okta_service import OktaService, OktaTimeout
from tests.factories import UserFactory


@pytest.fixture
def okta_service() -> OktaService:
    service = OktaService()
    service.initialize("fake.domain", "fake.token")
    return service


def _rate_limit_error() -> OktaAPIError:
    """A 429 error shaped like what the SDK returns after exhausting its retries."""
    response_details = MagicMock(status=429, headers={})
    return OktaAPIError(
        "https://fake.domain/api/v1/users/okta_id",
        response_details,
        {"errorCode": "E0000047", "errorSummary": "You have exceeded the rate limit"},
    )


async def test_socket_timeout_surfaces_as_okta_timeout(mocker: MockerFixture, okta_service: OktaService) -> None:
    """The SDK returns an aiohttp/asyncio timeout as an error; the facade raises OktaTimeout."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, None, asyncio.TimeoutError()))
    with pytest.raises(OktaTimeout):
        await okta_service.get_user("okta_id")


async def test_request_timeout_deadline_surfaces_as_okta_timeout(
    mocker: MockerFixture, okta_service: OktaService
) -> None:
    """The SDK's cumulative request-timeout deadline is mapped to OktaTimeout."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, None, Exception("Request Timeout exceeded.")))
    with pytest.raises(OktaTimeout):
        await okta_service.get_user("okta_id")


async def test_rate_limit_exhaustion_surfaces_as_okta_timeout(mocker: MockerFixture, okta_service: OktaService) -> None:
    """A 429 that outlived the SDK's rate-limit retries is surfaced as OktaTimeout."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, MagicMock(), _rate_limit_error()))
    with pytest.raises(OktaTimeout):
        await okta_service.get_user("okta_id")


async def test_swallowable_call_site_absorbs_timeout(mocker: MockerFixture, okta_service: OktaService) -> None:
    """Membership mutations catch OktaTimeout and continue instead of propagating."""
    mocker.patch("okta.client.Client.assign_user_to_group", return_value=(None, asyncio.TimeoutError()))
    # Should not raise — add_user_to_group swallows OktaTimeout.
    await okta_service.add_user_to_group("group_id", "user_id")


async def test_non_timeout_error_is_not_mapped(mocker: MockerFixture, okta_service: OktaService) -> None:
    """Errors that aren't timeouts or 429s pass through and are not misclassified as OktaTimeout."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, MagicMock(), Exception("boom")))
    with pytest.raises(Exception) as exc_info:
        await okta_service.get_user("okta_id")
    assert not isinstance(exc_info.value, OktaTimeout)


async def test_facade_adds_no_retry(mocker: MockerFixture, okta_service: OktaService) -> None:
    """The facade issues a single call; rate-limit (429) retries are the SDK's job."""
    success = (UserFactory(), None, None)
    mocked_request = mocker.patch("okta.client.Client.get_user", return_value=success)

    user = await okta_service.get_user("okta_id")

    assert user is not None
    assert mocked_request.call_count == 1
