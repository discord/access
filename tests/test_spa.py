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

import re
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from api import app as app_module
from api.app import _inject_csp_nonce, create_app
from api.config import settings
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


def test_inject_csp_nonce_stamps_inline_tags_and_webpack_global() -> None:
    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"></head>'
        "<body><style>.x{color:red}</style>"
        '<script type="module" crossorigin src="/assets/index-abc.js"></script></body></html>'
    )
    out = _inject_csp_nonce(html, "TESTNONCE")
    # Inline <script>/<style> the build emitted get the nonce...
    assert '<script nonce="TESTNONCE" type="module" crossorigin src="/assets/index-abc.js">' in out
    assert '<style nonce="TESTNONCE">.x{color:red}' in out
    # ...and styled-components' runtime injections are covered by seeding the
    # webpack nonce global first thing in <head>.
    assert '<head><script nonce="TESTNONCE">window.__webpack_nonce__="TESTNONCE"</script>' in out
    # No double-stamping of the setter script.
    assert out.count("window.__webpack_nonce__") == 1


async def test_spa_shell_injects_per_request_csp_nonce(spa_client: httpx.AsyncClient, stub_build_dir: Path) -> None:
    # serve_spa stamps the request's nonce into the shell regardless of env
    # (the header policy differs by env, but the body injection does not).
    (stub_build_dir / "index.html").write_text(
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"></head>'
        '<body><div id="root"></div>'
        '<script type="module" crossorigin src="/assets/index-abc.js"></script></body></html>'
    )
    resp = await spa_client.get("/apps/Github")
    assert resp.status_code == 200
    body = resp.text

    match = re.search(r'window\.__webpack_nonce__="([^"]+)"', body)
    assert match is not None, body
    nonce = match.group(1)
    # CSP header must authorize the exact nonce stamped into the shell.
    assert f"'nonce-{nonce}'" in resp.headers["content-security-policy"]
    # The module script the build emitted carries the nonce...
    assert f'<script nonce="{nonce}" type="module" crossorigin src="/assets/index-abc.js">' in body
    # ...and the webpack-nonce setter is the first thing inside <head>.
    assert f'<head><script nonce="{nonce}">window.__webpack_nonce__="{nonce}"</script>' in body


async def test_prod_csp_uses_nonce_and_drops_unsafe_inline(
    spa_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With DEBUG off, the app policy drops 'unsafe-inline' and authorizes inline
    # via the per-response nonce. The header is emitted regardless of the auth
    # outcome, so this holds without wiring up an authenticated SPA request.
    monkeypatch.setattr(settings, "ENV", "staging")
    resp = await spa_client.get("/apps/Github")
    csp = resp.headers["content-security-policy"]
    assert "unsafe-inline" not in csp
    assert re.search(r"script-src [^;]*'nonce-[^']+'", csp), csp
    assert re.search(r"style-src 'self' 'nonce-[^']+' https://fonts.googleapis.com", csp), csp


async def test_docs_path_keeps_relaxed_csp_in_prod(
    spa_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Swagger's inline bootstrap can't carry our nonce, so docs paths keep the
    # relaxed, CDN-allowing policy even when DEBUG is off.
    monkeypatch.setattr(settings, "ENV", "staging")
    resp = await spa_client.get("/api/openapi.json")
    csp = resp.headers["content-security-policy"]
    assert "'unsafe-inline'" in csp
    assert "cdn.jsdelivr.net" in csp
    assert "nonce-" not in csp
