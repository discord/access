"""Tests for the SPA catch-all route in `api.app.create_app` (`serve_spa`).

CI doesn't run the Vite build, so `api.app.BUILD_DIR` doesn't exist by
default and the catch-all route never registers against the shared `app`
fixture. Each test here builds its own app with `BUILD_DIR` monkeypatched to
a stub directory before `create_app()` runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import app as app_module
from api.app import create_app
from api.extensions import Db


@pytest.fixture
def stub_build_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    build_dir = tmp_path / "build"
    (build_dir / "assets").mkdir(parents=True)
    (build_dir / "index.html").write_text("<html><body>shell</body></html>")
    (build_dir / "assets" / "index-existing.js").write_text('console.log("hi");')
    monkeypatch.setattr(app_module, "BUILD_DIR", build_dir)
    return build_dir


@pytest.fixture
def spa_client(db: Db, stub_build_dir: Path) -> Generator[TestClient, None, None]:
    app: FastAPI = create_app(testing=True)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_existing_asset_is_served_with_immutable_cache_control(spa_client: TestClient) -> None:
    resp = spa_client.get("/assets/index-existing.js")
    assert resp.status_code == 200
    assert resp.text == 'console.log("hi");'
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in resp.headers["content-type"]


def test_missing_asset_returns_404_not_the_spa_shell(spa_client: TestClient) -> None:
    resp = spa_client.get("/assets/index-missing.js")
    assert resp.status_code == 404
    assert resp.headers["cache-control"] == "no-store"
    assert "shell" not in resp.text


def test_unknown_route_falls_back_to_spa_shell_without_caching(spa_client: TestClient) -> None:
    resp = spa_client.get("/apps/Github")
    assert resp.status_code == 200
    assert resp.text == "<html><body>shell</body></html>"
    assert resp.headers["cache-control"] == "no-cache, must-revalidate"
