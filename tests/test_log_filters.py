from __future__ import annotations

import logging

from api.log_filters import RedactingUvicornLogger


def _record(args: tuple) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=args,
        exc_info=None,
    )


def test_redacts_oidc_authorize_query() -> None:
    record = _record(("1.2.3.4", "GET", "/oidc/authorize?code=secret&state=xyz", "1.1", 302))
    assert RedactingUvicornLogger().filter(record) is True
    assert record.args[2] == "/oidc/authorize?[REDACTED]"


def test_redacts_oidc_authorize_subpaths() -> None:
    record = _record(("1.2.3.4", "GET", "/oidc/authorize-callback?code=secret", "1.1", 302))
    assert RedactingUvicornLogger().filter(record) is True
    assert record.args[2] == "/oidc/authorize-callback?[REDACTED]"


def test_passes_through_non_oidc_paths_with_query() -> None:
    record = _record(("1.2.3.4", "GET", "/api/users?page=0&per_page=20", "1.1", 200))
    assert RedactingUvicornLogger().filter(record) is True
    assert record.args[2] == "/api/users?page=0&per_page=20"


def test_passes_through_paths_without_query() -> None:
    record = _record(("1.2.3.4", "GET", "/api/users", "1.1", 200))
    assert RedactingUvicornLogger().filter(record) is True
    assert record.args[2] == "/api/users"


def test_ignores_unexpected_record_shape() -> None:
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="", lineno=0, msg="msg", args=(), exc_info=None
    )
    assert RedactingUvicornLogger().filter(record) is True
