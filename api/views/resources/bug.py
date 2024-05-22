from typing import Any, Dict
from urllib.parse import urlparse

import requests
from flask import current_app, request
from flask_apispec import MethodResource


# See more at
# https://docs.sentry.io/platforms/javascript/troubleshooting/#dealing-with-ad-blockers
# https://github.com/getsentry/examples/blob/master/tunneling/python/app.py
class SentryProxyResource(MethodResource):
    def post(self) -> Dict[str, Any]:
        if current_app.config["ENV"] not in ("development", "test") and current_app.config["REACT_SENTRY_DSN"]:
            envelope = request.data
            dsn = urlparse(current_app.config["REACT_SENTRY_DSN"])

            hostname = dsn.hostname
            project_id = dsn.path.strip("/")

            # Replace the client placeholder Sentry DSN with the one for the React app
            new_envelope = envelope.decode("utf-8").replace(
                "https://user@example.ingest.sentry.io/1234567",
                current_app.config["REACT_SENTRY_DSN"],
            )

            requests.post(
                url=f"https://{hostname}/api/{project_id}/envelope/",
                data=new_envelope,
                headers={"Content-Type": "application/x-sentry-envelope"},
            )

        return {}
