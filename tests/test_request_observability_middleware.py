from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

import api.middleware as middleware_module


@pytest.fixture
def fake_hook(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(middleware_module, "get_metrics_reporter_hook", lambda: fake)
    return fake


async def test_emits_counter_and_histogram_per_request(
    app: FastAPI, client: AsyncClient, db: Any, fake_hook: MagicMock
) -> None:
    rep = await client.get("/api/healthz")
    assert rep.status_code == 200

    fake_hook.record_counter.assert_called_once()
    counter_kwargs = fake_hook.record_counter.call_args.kwargs
    assert counter_kwargs == {"metric_name": "requests", "value": 1, "tags": {"method": "GET", "status": "200"}}

    fake_hook.record_histogram.assert_called_once()
    hist_kwargs = fake_hook.record_histogram.call_args.kwargs
    assert hist_kwargs["metric_name"] == "request.duration"
    assert isinstance(hist_kwargs["value"], float) and hist_kwargs["value"] >= 0
    assert hist_kwargs["tags"] == {"method": "GET", "status": "200", "unit": "ms"}


async def test_tags_record_non_2xx_status(app: FastAPI, client: AsyncClient, db: Any, fake_hook: MagicMock) -> None:
    rep = await client.get("/api/this-route-does-not-exist")
    assert rep.status_code == 404
    assert fake_hook.record_counter.call_args.kwargs["tags"]["status"] == "404"
    assert fake_hook.record_histogram.call_args.kwargs["tags"]["status"] == "404"


async def test_does_not_emit_an_access_log_line(
    app: FastAPI, client: AsyncClient, db: Any, fake_hook: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await client.get("/api/healthz")
    assert [r for r in caplog.records if r.name.startswith("api.middleware")] == []


async def test_request_survives_plugin_hook_failure(
    app: FastAPI, client: AsyncClient, db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> Any:
        raise RuntimeError("plugin manager not initialised")

    monkeypatch.setattr(middleware_module, "get_metrics_reporter_hook", boom)
    rep = await client.get("/api/healthz")
    assert rep.status_code == 200
