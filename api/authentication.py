import json
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from flask import Request, abort, current_app, g, redirect, session, url_for
from flask.typing import ResponseReturnValue
from sentry_sdk import set_user
from sqlalchemy import func

from api.extensions import oidc
from api.models import OktaUser


class AuthenticationHelpers:
    @staticmethod
    def authenticate_user(request: Request) -> Optional[ResponseReturnValue]:
        if current_app.config["ENV"] in ("development", "test"):
            # Bypass authentication for development and testing
            current_user = (
                OktaUser.query.filter(
                    func.lower(OktaUser.email) == func.lower(current_app.config["CURRENT_OKTA_USER_EMAIL"])
                )
                .filter(OktaUser.deleted_at.is_(None))
                .first_or_404()
            )
            g.current_user_id = current_user.id
        elif "CLOUDFLARE_TEAM_DOMAIN" in current_app.config:
            payload = CloudflareAuthenticationHelpers.verify_cloudflare_token(request)
            if "email" in payload:
                current_user = (
                    OktaUser.query.filter(func.lower(OktaUser.email) == func.lower(payload["email"]))
                    .filter(OktaUser.deleted_at.is_(None))
                    .first_or_404()
                )
                g.current_user_id = current_user.id
            elif "common_name" in payload:
                g.current_user_id = payload["common_name"]
        elif "OIDC_CLIENT_SECRETS" in current_app.config:
            # Bypass authentication for the login, logout, and authorize endpoints
            if request.path.startswith("/oidc/"):
                return None
            # Redirect to the OIDC login page if not logged in
            if not oidc.user_loggedin:
                # Copied from oidc.require_login decorator
                redirect_uri = "{login}?next={here}".format(
                    login=url_for("oidc_auth.login"),
                    here=quote_plus(request.url),
                )
                return redirect(redirect_uri)
            current_user = (
                OktaUser.query.filter(
                    func.lower(OktaUser.email) == func.lower(session["oidc_auth_profile"].get("email"))
                )
                .filter(OktaUser.deleted_at.is_(None))
                .first_or_404()
            )
            g.current_user_id = current_user.id
        else:
            abort(403, "No authentication method configured")

        if current_app.config["FLASK_SENTRY_DSN"]:
            set_user({"id": g.current_user_id})

        return None


class CloudflareAuthenticationHelpers:
    # https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/validating-json/
    # https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/application-token/
    @staticmethod
    def verify_cloudflare_token(request: Request) -> Dict[str, Any]:
        token = ""
        if "Cf-Access-Token" in request.headers:
            token = request.headers["Cf-Access-Token"]
        elif "Cf-Access-Jwt-Assertion" in request.headers:
            token = request.headers["Cf-Access-Jwt-Assertion"]
        elif "CF_Authorization" in request.cookies:
            token = request.cookies["CF_Authorization"]
        else:
            abort(403, "Missing required Cloudflare authorization token")

        unverified_payload = jwt.get_unverified_header(token)

        if "kid" not in unverified_payload:
            abort(403, "Invalid Cloudflare authorization token: Missing kid")

        keys = current_app.config["CLOUDFLARE_PUBLIC_KEYS"]

        if unverified_payload["kid"] not in keys:
            # If the kid is not in the cache, fetch the new key set
            keys = CloudflareAuthenticationHelpers.get_public_keys(current_app.config["CLOUDFLARE_TEAM_DOMAIN"])
            if unverified_payload["kid"] not in keys:
                abort(403, "Invalid Cloudflare authorization token: Invalid kid")

            # Cache the new key set
            current_app.config["CLOUDFLARE_PUBLIC_KEYS"] = keys

        payload = {}
        try:
            # decode returns the claims that has the email when needed
            payload = jwt.decode(
                token,
                key=keys[unverified_payload["kid"]],
                audience=current_app.config["CLOUDFLARE_APPLICATION_AUDIENCE"],
                algorithms=["RS256"],
            )
        except jwt.exceptions.DecodeError:
            abort(403, "Invalid Cloudflare authorization token: Invalid signature")

        return payload

    @staticmethod
    def get_public_keys(cloudflare_team_domain: str) -> Dict[str, RSAPrivateKey | RSAPublicKey]:
        """
        Returns:
            List of RSA public keys usable by PyJWT.
        """
        r = requests.get("https://{}/cdn-cgi/access/certs".format(cloudflare_team_domain))
        r.raise_for_status()
        public_keys = {}
        jwk_set = r.json()
        for key_dict in jwk_set["keys"]:
            public_keys[key_dict["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_dict))
        return public_keys
