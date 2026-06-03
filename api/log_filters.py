import logging
import re


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
