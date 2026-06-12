from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from pytest_mock import MockerFixture

from api.extensions import Db


async def test_health_check(app: FastAPI, client: AsyncClient, db: Db, url_for: Any) -> None:
    # test unauthenticated requests by setting the current user email to "Unauthenticated"
    app.state.current_user_email = "Unauthenticated"

    # test 200
    health_check_url = url_for("api-health-check.health_check")
    rep = await client.get(health_check_url)
    assert rep.status_code == 200


async def test_health_check_does_not_leak_db_error(
    app: FastAPI, client: AsyncClient, db: Db, mocker: MockerFixture, url_for: Any
) -> None:
    # /api/healthz is unauthenticated (in AUTH_ALLOWLIST_PREFIXES), so its
    # error path must not expose driver/connection details to the caller.
    app.state.current_user_email = "Unauthenticated"

    secret = "could not connect: host=db.internal dbname=access user=access_admin password=hunter2"
    mocker.patch.object(db.session, "execute", side_effect=Exception(secret))

    rep = await client.get(url_for("api-health-check.health_check"))
    assert rep.status_code == 500
    # The raw exception text must not reach an unauthenticated client.
    assert secret not in rep.text
    assert rep.json() == {"status": "error"}
