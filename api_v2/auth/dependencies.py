"""
FastAPI authentication dependencies.
Provides authentication and authorization logic converted from Flask.
"""

import json
import os
from typing import Any, Dict, Optional

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import OktaUser
from api_v2.database import get_db

# Security schemes
security = HTTPBearer(auto_error=False)


class CloudflareAuth:
    """Cloudflare Access authentication helper"""

    @staticmethod
    def get_public_keys(cloudflare_team_domain: str) -> Dict[str, RSAPrivateKey | RSAPublicKey]:
        """
        Fetch and parse Cloudflare public keys for JWT verification.

        Returns:
            Dictionary mapping kid to RSA public keys usable by PyJWT.
        """
        r = requests.get(f"https://{cloudflare_team_domain}/cdn-cgi/access/certs")
        r.raise_for_status()
        public_keys = {}
        jwk_set = r.json()
        for key_dict in jwk_set["keys"]:
            public_keys[key_dict["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_dict))
        return public_keys

    @staticmethod
    def verify_cloudflare_token(request: Request) -> Dict[str, Any]:
        """
        Verify Cloudflare Access token from request headers or cookies.

        Args:
            request: FastAPI Request object

        Returns:
            JWT payload containing user information

        Raises:
            HTTPException: If token is missing or invalid
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

        # Get public keys from environment or fetch them
        cloudflare_team_domain = os.getenv("CLOUDFLARE_TEAM_DOMAIN")
        if not cloudflare_team_domain:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cloudflare team domain not configured"
            )

        # TODO: In a real implementation, you'd want to cache these keys
        keys = CloudflareAuth.get_public_keys(cloudflare_team_domain)

        if unverified_payload["kid"] not in keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Cloudflare authorization token: Invalid kid"
            )

        # Verify and decode the token
        try:
            cloudflare_audience = os.getenv("CLOUDFLARE_APPLICATION_AUDIENCE")
            if not cloudflare_audience:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Cloudflare application audience not configured",
                )

            payload = jwt.decode(
                token,
                key=keys[unverified_payload["kid"]],
                audience=cloudflare_audience,
                algorithms=["RS256"],
            )
            return payload
        except jwt.exceptions.DecodeError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Cloudflare authorization token: Invalid signature",
            )


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> OktaUser:
    """
    Get the current authenticated user.
    This is the main authentication dependency that replicates Flask's authenticate_user.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        OktaUser object

    Raises:
        HTTPException: If authentication fails
    """
    env = os.getenv("FLASK_ENV", os.getenv("ENV", "production"))

    # Development/test mode - bypass authentication
    if env in ("development", "test"):
        test_email = os.getenv("CURRENT_OKTA_USER_EMAIL")
        if not test_email:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Test user email not configured"
            )

        current_user = (
            db.query(OktaUser)
            .filter(func.lower(OktaUser.email) == func.lower(test_email))
            .filter(OktaUser.deleted_at.is_(None))
            .first()
        )
        if not current_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test user not found")
        return current_user

    # Cloudflare Access authentication
    elif os.getenv("CLOUDFLARE_TEAM_DOMAIN"):
        payload = CloudflareAuth.verify_cloudflare_token(request)

        if "email" in payload:
            current_user = (
                db.query(OktaUser)
                .filter(func.lower(OktaUser.email) == func.lower(payload["email"]))
                .filter(OktaUser.deleted_at.is_(None))
                .first()
            )
            if not current_user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            return current_user
        elif "common_name" in payload:
            # For cases where we only have common_name, we need to handle this differently
            # This might need to be adjusted based on your specific use case
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Common name authentication not fully implemented"
            )
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token payload")

    # OIDC authentication - for now just raise an error, implement if needed
    elif os.getenv("OIDC_CLIENT_SECRETS"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="OIDC authentication not yet implemented in FastAPI"
        )

    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No authentication method configured")


# Optional dependency for endpoints that don't require authentication
async def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[OktaUser]:
    """
    Get current user without raising exceptions if not authenticated.
    Useful for endpoints that have optional authentication.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        OktaUser if authenticated, None otherwise
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None
