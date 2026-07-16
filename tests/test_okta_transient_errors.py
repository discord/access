import asyncio
from unittest.mock import MagicMock

import pytest
from okta.errors.http_error import HTTPError
from okta.errors.okta_api_error import OktaAPIError
from pytest_mock import MockerFixture

from api.services.okta_service import OktaService, OktaTransientError
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


def _http_error(status: int) -> HTTPError:
    """An error shaped like what the SDK returns for a non-JSON HTTP failure —
    e.g. a 502 Bad Gateway whose body is an HTML page rather than an Okta error."""
    response_details = MagicMock(status=status, headers={})
    return HTTPError("https://fake.domain/api/v1/users/okta_id", response_details, f"<html>{status}</html>")


async def test_socket_timeout_surfaces_as_okta_timeout(mocker: MockerFixture, okta_service: OktaService) -> None:
    """The SDK returns an aiohttp/asyncio timeout as an error; the facade raises OktaTransientError."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, None, asyncio.TimeoutError()))
    with pytest.raises(OktaTransientError):
        await okta_service.get_user("okta_id")


async def test_request_timeout_deadline_surfaces_as_okta_timeout(
    mocker: MockerFixture, okta_service: OktaService
) -> None:
    """The SDK's cumulative request-timeout deadline is mapped to OktaTransientError."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, None, Exception("Request Timeout exceeded.")))
    with pytest.raises(OktaTransientError):
        await okta_service.get_user("okta_id")


async def test_rate_limit_exhaustion_surfaces_as_okta_timeout(mocker: MockerFixture, okta_service: OktaService) -> None:
    """A 429 that outlived the SDK's rate-limit retries is surfaced as OktaTransientError."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, MagicMock(), _rate_limit_error()))
    with pytest.raises(OktaTransientError):
        await okta_service.get_user("okta_id")


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_transient_5xx_surfaces_as_okta_timeout(
    mocker: MockerFixture, okta_service: OktaService, status: int
) -> None:
    """A transient 5xx (e.g. a 502 Bad Gateway from the load balancer) is surfaced
    as OktaTransientError so the syncer/fan-out swallow it instead of logging an ERROR."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, MagicMock(), _http_error(status)))
    with pytest.raises(OktaTransientError):
        await okta_service.get_user("okta_id")


async def test_non_transient_http_error_not_mapped(mocker: MockerFixture, okta_service: OktaService) -> None:
    """A non-transient HTTP error (e.g. 404) passes through and is not mapped to OktaTransientError."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, MagicMock(), _http_error(404)))
    with pytest.raises(Exception) as exc_info:
        await okta_service.get_user("okta_id")
    assert not isinstance(exc_info.value, OktaTransientError)


async def test_swallowable_call_site_absorbs_timeout(mocker: MockerFixture, okta_service: OktaService) -> None:
    """Membership mutations catch OktaTransientError and continue instead of propagating."""
    mocker.patch("okta.client.Client.assign_user_to_group", return_value=(None, asyncio.TimeoutError()))
    # Should not raise — add_user_to_group swallows OktaTransientError.
    await okta_service.add_user_to_group("group_id", "user_id")


async def test_non_timeout_error_is_not_mapped(mocker: MockerFixture, okta_service: OktaService) -> None:
    """Errors that aren't timeouts or 429s pass through and are not misclassified as OktaTransientError."""
    mocker.patch("okta.client.Client.get_user", return_value=(None, MagicMock(), Exception("boom")))
    with pytest.raises(Exception) as exc_info:
        await okta_service.get_user("okta_id")
    assert not isinstance(exc_info.value, OktaTransientError)


async def test_facade_adds_no_retry(mocker: MockerFixture, okta_service: OktaService) -> None:
    """The facade issues a single call; rate-limit (429) retries are the SDK's job."""
    success = (UserFactory(), None, None)
    mocked_request = mocker.patch("okta.client.Client.get_user", return_value=success)

    user = await okta_service.get_user("okta_id")

    assert user is not None
    assert mocked_request.call_count == 1
