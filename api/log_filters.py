import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime

from api.context import get_process_source, get_request_context


class TokenSanitizingFilter(logging.Filter):
    """Filter that redacts sensitive token information from application logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            msg = record.msg

            # OIDC libraries occasionally log the full refresh-token blob on
            # error. Redact the whole dict.
            if "Could not refresh token" in msg and "{" in msg:
                msg = re.sub(r"Could not refresh token (\{.*?\})", "Could not refresh token {REDACTED_TOKEN_DATA}", msg)

            if "code=" in msg:
                msg = re.sub(r'(code=)[^&"\s]*([&"\s]|$)', r"\1[REDACTED_AUTH_CODE]\2", msg)

            record.msg = msg

        return True


class RedactingUvicornLogger(logging.Filter):
    """Strip the query string from /oidc/authorize* access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        full_path = args[2]
        if not isinstance(full_path, str) or "?" not in full_path:
            return True
        path, _, _ = full_path.partition("?")
        if not path.startswith("/oidc/authorize"):
            return True
        record.args = (args[0], args[1], f"{path}?[REDACTED]", *args[3:])
        return True


_APP_LOGGER_ROOTS = ("api", "access")
_plugin_logger_prefixes: set[str] = set()


def register_plugin_logger_prefixes(prefixes: Iterable[str]) -> None:
    """Mark logger-name prefixes as belonging to installed plugins.

    Args:
        prefixes: Top-level package names of plugin modules. A logger named
            exactly a prefix, or nested under one (`prefix.sub`), is attributed
            to that plugin by `LogOriginFilter`.
    """
    _plugin_logger_prefixes.update(prefixes)


def _logger_root(name: str) -> str:
    """Return the top-level component of a dotted logger name."""
    return name.partition(".")[0]


def log_origin(logger_name: str) -> str:
    """Classify a logger name by whose code emitted it.

    Args:
        logger_name: The `LogRecord.name` to classify.

    Returns:
        `app` for Access's own loggers, `plugin:<package>` for a registered
        plugin package, otherwise `lib:<top-level logger>`.
    """
    root = _logger_root(logger_name)
    if root in _APP_LOGGER_ROOTS:
        return "app"
    if root in _plugin_logger_prefixes:
        return f"plugin:{root}"
    return f"lib:{root}"


class LogOriginFilter(logging.Filter):
    """Stamp `access_source`, `access_origin`, and `access_request_id` on every record.

    `access_source` is who invoked the work: the request's source (`web`,
    `mcp`) inside a request, else the process source (`server`,
    `cli:<command>`). `access_origin` is whose code logged it (see
    `log_origin`). Attach to handlers, not loggers, so records propagated
    from child loggers are stamped too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = get_request_context()
        record.access_source = ctx.source if ctx is not None else (get_process_source() or "unknown")
        record.access_origin = log_origin(record.name)
        record.access_request_id = ctx.request_id if ctx is not None else None
        return True


TEXT_LOG_FORMAT = "[%(access_source)s] [%(access_origin)s] %(levelname)s %(name)s: %(message)s"


class JsonLogFormatter(logging.Formatter):
    """Format each record as one JSON object per line.

    Field names follow what common log pipelines parse natively (`message`,
    `severity`). Requires `LogOriginFilter` on the same handler.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "source": getattr(record, "access_source", None),
            "origin": getattr(record, "access_origin", None),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "access_request_id", None)
        if request_id is not None:
            entry["request_id"] = request_id
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(entry, default=str)
