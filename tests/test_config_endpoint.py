"""Tests for the runtime app-config endpoint (GET /api/config).

The endpoint surfaces the IdP deep-link templates to the frontend at
runtime (read from `settings`), so the same built bundle can point at
different IdP consoles per deployment env.
"""

import pytest
from fastapi.testclient import TestClient

from api.config import settings


def test_config_endpoint_returns_idp_settings(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "IDP_NAME", "Okta")
    monkeypatch.setattr(
        settings, "IDP_USER_URL_TEMPLATE", "https://example-admin.okta.com/admin/user/profile/view/{id}"
    )
    monkeypatch.setattr(settings, "IDP_GROUP_URL_TEMPLATE", "https://example-admin.okta.com/admin/group/{id}")

    rep = client.get("/api/config")

    assert rep.status_code == 200, rep.text
    idp = rep.json()["idp"]
    assert idp["name"] == "Okta"
    assert idp["user_url_template"] == "https://example-admin.okta.com/admin/user/profile/view/{id}"
    assert idp["group_url_template"] == "https://example-admin.okta.com/admin/group/{id}"


def test_config_endpoint_defaults_to_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "IDP_NAME", "")
    monkeypatch.setattr(settings, "IDP_USER_URL_TEMPLATE", "")
    monkeypatch.setattr(settings, "IDP_GROUP_URL_TEMPLATE", "")

    rep = client.get("/api/config")

    assert rep.status_code == 200, rep.text
    idp = rep.json()["idp"]
    assert idp == {"name": "", "user_url_template": "", "group_url_template": ""}
