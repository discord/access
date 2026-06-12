"""Tests for the SPA catch-all route in `api.app.create_app` (`serve_spa`).

CI doesn't run the Vite build, so `api.app.BUILD_DIR` doesn't exist by
default and the catch-all route never registers against the shared `app`
fixture. Each test here builds its own app with `BUILD_DIR` monkeypatched to
a stub directory before `create_app()` runs.

Uses an in-process `httpx.AsyncClient` (not Starlette's sync `TestClient`):
every request runs `RequestIdMiddleware`, which awaits `db.remove()` on the
async engine bound by the `db` fixture. `TestClient` would drive the app on
its own portal-thread event loop, and asyncpg connections are loop-bound, so
that cross-loop use raises "another operation is in progress".
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

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
async def spa_client(db: Db, stub_build_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(testing=True)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as c:
        yield c


async def test_existing_asset_is_served_with_immutable_cache_control(spa_client: httpx.AsyncClient) -> None:
    resp = await spa_client.get("/assets/index-existing.js")
    assert resp.status_code == 200
    assert resp.text == 'console.log("hi");'
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in resp.headers["content-type"]


async def test_missing_asset_returns_404_not_the_spa_shell(spa_client: httpx.AsyncClient) -> None:
    resp = await spa_client.get("/assets/index-missing.js")
    assert resp.status_code == 404
    assert resp.headers["cache-control"] == "no-store"
    assert "shell" not in resp.text


async def test_unknown_route_falls_back_to_spa_shell_without_caching(spa_client: httpx.AsyncClient) -> None:
    resp = await spa_client.get("/apps/Github")
    assert resp.status_code == 200
    assert resp.text == "<html><body>shell</body></html>"
    assert resp.headers["cache-control"] == "no-cache, must-revalidate"
