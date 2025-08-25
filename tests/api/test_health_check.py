from flask import Flask, url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy


def test_health_check(app: Flask, client: FlaskClient, db: SQLAlchemy) -> None:
    # test unauthenticated requests by setting the current user email to "Unauthenticated"
    app.config["CURRENT_OKTA_USER_EMAIL"] = "Unauthenticated"

    # test 200
    health_check_url = url_for("api-health-check.health_check")
    rep = client.get(health_check_url)
    assert rep.status_code == 200
