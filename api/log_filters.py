import logging
import re


class TokenSanitizingFilter(logging.Filter):
    """Filter that redacts sensitive token information from log messages."""

    def filter(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            # Check for token logging pattern from flask_oidc
            if "Could not refresh token" in record.msg and "{" in record.msg:
                # Replace the entire token dictionary with a placeholder
                record.msg = re.sub(
                    r"Could not refresh token (\{.*?\})", "Could not refresh token {REDACTED_TOKEN_DATA}", record.msg
                )
        return True
