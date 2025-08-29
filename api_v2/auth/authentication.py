import json
from functools import lru_cache
from typing import Any, Dict

import httpx
import jwt
from async_lru import alru_cache
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import HTTPException, Request, status

from api_v2.config import settings


class CloudflareAuth:
    """Cloudflare Access authentication helper"""

    def __init__(self):
        self.team_domain = settings.cloudflare_team_domain
        self.application_audience = settings.cloudflare_application_audience

        if not self.team_domain:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cloudflare team domain not configured"
            )
        if not self.application_audience:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cloudflare application audience not configured",
            )

    @alru_cache(maxsize=1, ttl=60 * 60 * 24)  # 24 hours TTL
    async def _get_public_keys(self) -> Dict[str, RSAPrivateKey | RSAPublicKey]:
        """
        Fetch and parse Cloudflare public keys for JWT verification (cached).

        Returns:
            Dictionary mapping kid to RSA public keys usable by PyJWT.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://{self.team_domain}/cdn-cgi/access/certs")
            response.raise_for_status()
            public_keys = {}
            jwk_set = response.json()
            for key_dict in jwk_set["keys"]:
                public_keys[key_dict["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_dict))

        return public_keys

    async def verify_cloudflare_request(self, request: Request) -> Dict[str, Any]:
        """
        Verify Cloudflare Access token from request headers or cookies.
        """
        token = ""

        # Check for token in headers or cookies
        if "cf-access-token" in request.headers:
            token = request.headers["cf-access-token"]
        elif "cf-access-jwt-assertion" in request.headers:
            token = request.headers["cf-access-jwt-assertion"]
        elif "CF_Authorization" in request.cookies:
            token = request.cookies["CF_Authorization"]
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Missing required Cloudflare authorization token"
            )

        return await self.verify_cloudflare_token(token)

    @alru_cache(ttl=60)  # 1 minute TTL for token verification as they expire periodically
    async def verify_cloudflare_token(self, token: str) -> Dict[str, Any]:
        """
        Verify Cloudflare Access token from request headers or cookies.

        Args:
            request: FastAPI Request object

        Returns:
            JWT payload containing user information

        Raises:
            HTTPException: If token is missing or invalid
        """

        # Get unverified header to extract kid
        try:
            unverified_payload = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Cloudflare authorization token format"
            )

        if "kid" not in unverified_payload:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Cloudflare authorization token: Missing kid"
            )

        # Get cached public keys
        keys = await self._get_public_keys()

        if unverified_payload["kid"] not in keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Cloudflare authorization token: Invalid kid"
            )

        # Verify and decode the token
        try:
            payload = jwt.decode(
                token,
                key=keys[unverified_payload["kid"]],
                audience=self.application_audience,
                algorithms=["RS256"],
            )
            return payload
        except jwt.exceptions.DecodeError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Cloudflare authorization token: Invalid signature",
            )


class OIDCAuth:
    """OIDC authentication helper"""

    def __init__(self):
        # Load and validate configuration on initialization
        self.config = self._load_oidc_config()

    @lru_cache(maxsize=1)
    def _load_oidc_config(self) -> Dict[str, Any]:
        """Load OIDC configuration from environment (cached)"""
        if not settings.oidc_client_secrets:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OIDC_CLIENT_SECRETS not configured"
            )

        try:
            # Handle both inline JSON and file path
            if settings.oidc_client_secrets.startswith("{") and settings.oidc_client_secrets.endswith("}"):
                # Inline JSON
                client_secrets = json.loads(settings.oidc_client_secrets)
            else:
                # File path
                with open(settings.oidc_client_secrets, "r") as f:
                    client_secrets = json.load(f)

            # Extract configuration
            if "web" in client_secrets:
                issuer = client_secrets["web"]["issuer"]
                client_id = client_secrets["web"]["client_id"]
            else:
                issuer = client_secrets.get("issuer")
                client_id = client_secrets.get("client_id")

            if not issuer or not client_id:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid OIDC client secrets: missing issuer or client_id",
                )

            # Create OpenID Connect discovery URL
            openid_connect_url = settings.oidc_server_metadata_url or f"{issuer}/.well-known/openid_configuration"

            config = {"issuer": issuer, "client_id": client_id, "openid_connect_url": openid_connect_url}

            return config

        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Invalid JSON in OIDC client secrets: {e}"
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OIDC client secrets file not found"
            )

    async def verify_oidc_request(self, request: Request) -> Dict[str, Any]:
        """
        Verify OIDC JWT token from request headers and return user email.
        """
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authorization header for OIDC"
            )

        token = auth_header.split(" ", 1)[1]

        return await self.verify_oidc_token(token)

    @alru_cache(ttl=60)  # 1 minute TTL for token verification as they expire periodically
    async def verify_oidc_token(self, token: str) -> Dict[str, Any]:
        """
        Verify OIDC JWT token from request headers and return user email.

        Args:
            request: FastAPI Request object

        Returns:
            User email from the OIDC token

        Raises:
            HTTPException: If token is missing or invalid
        """

        try:
            # Get unverified header to extract kid
            unverified_header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC token format")

        # Get cached JWKS
        jwks = await self._get_jwks()

        # Find the correct key
        key = None
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == unverified_header.get("kid"):
                key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
                break

        if not key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to find matching key for OIDC token"
            )

        # Validate and decode the token
        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.config["client_id"],
                issuer=self.config["issuer"],
                leeway=settings.oidc_clock_skew,
            )

            return payload

        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token has expired")
        except jwt.InvalidAudienceError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC token audience")
        except jwt.InvalidIssuerError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC token issuer")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid OIDC token: {e}")

    @alru_cache(maxsize=1, ttl=60 * 60 * 24)  # 24 hours TTL
    async def _get_jwks(self) -> Dict[str, Any]:
        """Get JWKS from discovery document (cached)"""
        async with httpx.AsyncClient() as client:
            try:
                # Get discovery document
                discovery_response = await client.get(self.config["openid_connect_url"])
                discovery_response.raise_for_status()
                discovery = discovery_response.json()

                jwks_uri = discovery.get("jwks_uri")
                if not jwks_uri:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="JWKS URI not found in OIDC discovery document",
                    )

                # Get JWKS
                jwks_response = await client.get(jwks_uri)
                jwks_response.raise_for_status()
                jwks = jwks_response.json()

                return jwks

            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch OIDC configuration: {e}"
                )


@lru_cache(maxsize=1)
def get_cloudflare_auth() -> CloudflareAuth:
    """Get or create CloudflareAuth instance (memoized)"""
    return CloudflareAuth()


@lru_cache(maxsize=1)
def get_oidc_auth() -> OIDCAuth:
    """Get or create OIDCAuth instance (memoized)"""
    return OIDCAuth()
