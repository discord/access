"""Tests for the Slack notifications plugin."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_PLUGIN_PATH = Path(__file__).with_name("notifications_slack.py")


def _load_plugin(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    """Import a fresh copy of the plugin with ``env`` set and the Slack SDK stubbed.

    The plugin reads its configuration and builds its Slack client at import
    time, so each test imports it anew under the environment it needs.
    """
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    for name in ("CLIENT_ORIGIN_URL", "ENV"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in ("slack_sdk", "slack_sdk.web", "slack_sdk.web.async_client"):
        monkeypatch.setitem(sys.modules, name, MagicMock())

    spec = importlib.util.spec_from_file_location("notifications_slack_under_test", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_base_url_prefers_client_origin_url(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _load_plugin(monkeypatch, CLIENT_ORIGIN_URL="https://access.example.com", ENV="production")
    assert plugin.get_base_url() == "https://access.example.com"


def test_base_url_in_production_without_client_origin_url(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _load_plugin(monkeypatch, ENV="production")
    assert plugin.get_base_url() == "https://example.com"


def test_base_url_defaults_to_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _load_plugin(monkeypatch)
    assert plugin.get_base_url() == "http://localhost:3000"
