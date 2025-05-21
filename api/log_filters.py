import logging
import re
from typing import Any, Dict

from gunicorn.glogging import Logger as GunicornLogger


class TokenSanitizingFilter(logging.Filter):
    """Filter that redacts sensitive token information from Flask application logs.

    Note: This filter handles internal application logs. For HTTP access logs,
    see gunicorn_logging.py which provides specialized handling for those.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            msg = record.msg

            # Check for token logging pattern from flask_oidc
            if "Could not refresh token" in msg and "{" in msg:
                # Replace the entire token dictionary with a placeholder
                msg = re.sub(r"Could not refresh token (\{.*?\})", "Could not refresh token {REDACTED_TOKEN_DATA}", msg)

            # This provides defense-in-depth alongside the Gunicorn logger
            if "code=" in msg:
                msg = re.sub(r'(code=)[^&"\s]*([&"\s]|$)', r"\1[REDACTED_AUTH_CODE]\2", msg)

            record.msg = msg

        return True


# Ignore mypy error as GunicornLogger lacks proper type annotations in gunicorn stubs
class RedactingGunicornLogger(GunicornLogger):  # type: ignore[misc]
    """
    Gunicorn logger that strips query strings from /oidc/authorize access logs.
    """

    def access(self, resp: Any, req: Any, environ: Dict[str, Any], request_time: float) -> None:
        path = environ.get("PATH_INFO", "")
        query = environ.get("QUERY_STRING", "")

        if path.startswith("/oidc/authorize"):
            # Override WSGI variable used by Gunicorn's access log formatter
            environ["RAW_URI"] = f"{path}?[REDACTED]"
        else:
            # Optional: Set RAW_URI for other paths to preserve default behavior
            # so Gunicorn doesn't construct it from PATH_INFO + QUERY_STRING
            environ["RAW_URI"] = f"{path}?{query}" if query else path

        super().access(resp, req, environ, request_time)
