import logging
import re


class TokenSanitizingFilter(logging.Filter):
    """Filter that redacts sensitive token information from FastAPI application logs.

    This filter prevents sensitive authentication tokens, authorization codes,
    and other credentials from appearing in application logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            msg = record.msg

            # Check for token logging pattern from flask_oidc (if used)
            if "Could not refresh token" in msg and "{" in msg:
                # Replace the entire token dictionary with a placeholder
                msg = re.sub(r"Could not refresh token (\{.*?\})", "Could not refresh token {REDACTED_TOKEN_DATA}", msg)

            # Redact authorization codes from query strings
            if "code=" in msg:
                msg = re.sub(r'(code=)[^&"\s]*([&"\s]|$)', r"\1[REDACTED_AUTH_CODE]\2", msg)

            # Redact Bearer tokens from Authorization headers
            if "Authorization:" in msg or "authorization:" in msg:
                msg = re.sub(r'(authorization:\s*bearer\s+)[^\s"]+', r'\1[REDACTED_TOKEN]', msg, flags=re.IGNORECASE)

            # Redact Cloudflare Access tokens
            if "cf-access-token" in msg or "CF_Authorization" in msg:
                msg = re.sub(r'(cf-access-token["\s]*[:=]["\s]*)[^"\s]+', r'\1[REDACTED_CF_TOKEN]', msg, flags=re.IGNORECASE)
                msg = re.sub(r'(CF_Authorization["\s]*[:=]["\s]*)[^"\s]+', r'\1[REDACTED_CF_TOKEN]', msg, flags=re.IGNORECASE)

            record.msg = msg

        return True