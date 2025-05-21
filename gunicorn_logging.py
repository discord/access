import re

from gunicorn import glogging


class SanitizedAccessLogger(glogging.Logger):
    """Custom Gunicorn logger that sanitizes sensitive information in access logs.

    This logger intercepts access logs before they're output and redacts sensitive
    information like OAuth authorization codes.
    """

    def access(self, resp, req, environ, request_time):
        """Overrides the access method to sanitize log output."""
        # Store the original request URI
        original_uri = req.uri

        # Check if this is an authorization URI with a code
        if "code=" in req.uri:
            # Replace the authorization code with a placeholder
            req.uri = re.sub(r'(code=)[A-Za-z0-9_-]+([&\s"]|$)', r"\1[REDACTED_AUTH_CODE]\2", req.uri)

        # Call the parent class access method with the sanitized URI
        log_message = super().access(resp, req, environ, request_time)

        # Restore the original URI so we don't affect request processing
        req.uri = original_uri

        return log_message
