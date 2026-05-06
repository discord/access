from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.extensions import Db


def test_health_check(app: FastAPI, client: TestClient, db: Db, url_for: Any) -> None:
    # test unauthenticated requests by setting the current user email to "Unauthenticated"
    app.state.current_user_email = "Unauthenticated"

    # test 200
    health_check_url = url_for("api-health-check.health_check")
    rep = client.get(health_check_url)
    assert rep.status_code == 200
