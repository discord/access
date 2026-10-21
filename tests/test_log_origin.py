"""Log-line attribution: who invoked the work and whose code logged it."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from api import context, log_filters
from api.app import _audit_handler, _configure_logging
from api.cli import app_context
from api.context import RequestContext, reset_request_context, set_request_context
from api.extensions import Db
from api.log_filters import JsonLogFormatter, LogOriginFilter, log_origin, register_plugin_logger_prefixes
from api.plugins import _register_plugin_loggers
from api.schemas import AuditLogSchema, EventType


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Iterator[None]:
    """Undo the process-global changes `_configure_logging` makes."""
    source = context.get_process_source()
    prefixes = set(log_filters._plugin_logger_prefixes)
    formatters = [(handler, handler.formatter) for handler in logging.root.handlers]
    audit_logger = logging.getLogger("access.audit")
    audit_propagate = audit_logger.propagate
    yield
    context.set_process_source(source)
    log_filters._plugin_logger_prefixes.clear()
    log_filters._plugin_logger_prefixes.update(prefixes)
    for handler, formatter in formatters:
        handler.setFormatter(formatter)
    audit_logger.removeHandler(_audit_handler)
    audit_logger.propagate = audit_propagate


def _record(name: str = "api.syncer", msg: str = "hello") -> logging.LogRecord:
    record = logging.LogRecord(name, logging.INFO, __file__, 1, msg, None, None)
    LogOriginFilter().filter(record)
    return record


def test_log_origin_classifies_app_plugin_and_library_loggers() -> None:
    register_plugin_logger_prefixes(["example_plugin"])

    assert log_origin("api") == "app"
    assert log_origin("api.operations.create_group") == "app"
    assert log_origin("access.audit") == "app"
    assert log_origin("example_plugin") == "plugin:example_plugin"
    assert log_origin("example_plugin.helpers") == "plugin:example_plugin"
    assert log_origin("sqlalchemy.engine") == "lib:sqlalchemy"
    # A shared prefix that isn't a package boundary isn't the plugin.
    assert log_origin("example_plugin_other") == "lib:example_plugin_other"


def test_filter_prefers_request_source_over_process_source() -> None:
    context.set_process_source("server")
    token = set_request_context(RequestContext(request_id="req-1", user_agent=None, ip=None, source="mcp"))
    try:
        record = _record()
    finally:
        reset_request_context(token)

    assert record.access_source == "mcp"
    assert record.access_request_id == "req-1"


def test_filter_falls_back_to_process_source_outside_a_request() -> None:
    context.set_process_source("cli:sync")
    record = _record()

    assert record.access_source == "cli:sync"
    assert record.access_origin == "app"
    assert record.access_request_id is None


def test_json_formatter_emits_attribution_fields() -> None:
    context.set_process_source("cli:notify")
    entry = json.loads(JsonLogFormatter().format(_record("api.syncer", "synced %s")))

    assert entry["source"] == "cli:notify"
    assert entry["origin"] == "app"
    assert entry["logger"] == "api.syncer"
    assert entry["severity"] == "INFO"
    assert entry["message"] == "synced %s"
    assert "request_id" not in entry


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_audit_lines_bypass_the_root_formatter(monkeypatch: pytest.MonkeyPatch, log_format: str) -> None:
    monkeypatch.setenv("LOG_FORMAT", log_format)
    _configure_logging()

    audit_logger = logging.getLogger("access.audit")
    assert _audit_handler in audit_logger.handlers
    assert audit_logger.propagate is False
    line = '{"event_type": "group_create"}'
    assert _audit_handler.format(logging.LogRecord("access.audit", logging.INFO, __file__, 1, line, None, None)) == line


def test_plain_format_leaves_audit_on_the_root_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    _configure_logging()
    monkeypatch.setenv("LOG_FORMAT", "plain")
    _configure_logging()

    audit_logger = logging.getLogger("access.audit")
    assert _audit_handler not in audit_logger.handlers
    assert audit_logger.propagate is True


def test_audit_source_defaults_to_process_source_outside_a_request() -> None:
    context.set_process_source("cli:sync")
    payload = json.loads(AuditLogSchema().dumps({"event_type": EventType.group_create}))

    assert payload["source"] == "cli:sync"


async def test_app_context_sets_the_process_source(db: Db) -> None:
    async with app_context(scope="probe-log-source", log_source="cli:probe"):
        assert context.get_process_source() == "cli:probe"


def test_plugin_entry_points_register_their_top_level_package(mocker: MockerFixture) -> None:
    mocker.patch(
        "api.plugins.entry_points",
        side_effect=lambda group: [SimpleNamespace(module=f"pkg_{group.replace('.', '_')}.hooks")],
    )
    _register_plugin_loggers()

    assert log_origin("pkg_access_notifications.client") == "plugin:pkg_access_notifications"
    assert log_origin("pkg_access_commands") == "plugin:pkg_access_commands"
