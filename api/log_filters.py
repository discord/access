import logging
import re


class TokenSanitizingFilter(logging.Filter):
    """Filter that redacts sensitive token information from Flask application logs.

    Note: This filter handles internal application logs. For HTTP access logs,
    see gunicorn_logging.py which provides specialized handling for those.
    """

    def filter(self, record):
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
