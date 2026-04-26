"""Cloudflare Access JWT verification.

Sync implementation of the keysmith pattern. Fetches and caches signing keys
from `https://<team_domain>/cdn-cgi/access/certs`, refreshes on `kid`
rotation. Used by `get_current_user_id` for both human users (email claim)
and service tokens (common_name claim).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import jwt
import requests
from cachetools import TTLCache, cached
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import HTTPException
from starlette.requests import Request

from api.config import settings

logger = logging.getLogger(__name__)


@cached(cache=TTLCache(maxsize=1, ttl=3600))
def _signing_keys(team_domain: str) -> Dict[str, RSAPrivateKey | RSAPublicKey]:
    r = requests.get(
        f"https://{team_domain}/cdn-cgi/access/certs",
        timeout=10,
    )
    r.raise_for_status()
    keys: Dict[str, RSAPrivateKey | RSAPublicKey] = {}
    for key_dict in r.json()["keys"]:
        keys[key_dict["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_dict))
    return keys


def _refresh_keys(team_domain: str) -> Dict[str, RSAPrivateKey | RSAPublicKey]:
    _signing_keys.cache_clear()
    return _signing_keys(team_domain)


def extract_token(request: Request) -> str:
    return (
        request.headers.get("Cf-Access-Token")
        or request.headers.get("Cf-Access-Jwt-Assertion")
        or request.cookies.get("CF_Authorization")
        or ""
    )


def verify_cloudflare_token(token: str) -> Dict[str, Any]:
    """Verify a Cloudflare Access JWT and return its decoded payload.

    Raises HTTPException(403) on any verification failure.
    """
    if not settings.CLOUDFLARE_TEAM_DOMAIN:
        raise HTTPException(status_code=403, detail="Cloudflare Access not configured")
    try:
        unverified = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as e:
        raise HTTPException(status_code=403, detail="Invalid Cloudflare authorization token") from e
    kid: Optional[str] = unverified.get("kid")
    if not kid:
        raise HTTPException(status_code=403, detail="Invalid Cloudflare authorization token: Missing kid")

    keys = _signing_keys(settings.CLOUDFLARE_TEAM_DOMAIN)
    if kid not in keys:
        keys = _refresh_keys(settings.CLOUDFLARE_TEAM_DOMAIN)
        if kid not in keys:
            raise HTTPException(
                status_code=403,
                detail="Invalid Cloudflare authorization token: Invalid kid",
            )

    try:
        return jwt.decode(
            token,
            key=keys[kid],
            audience=settings.CLOUDFLARE_APPLICATION_AUDIENCE,
            algorithms=["RS256"],
        )
    except jwt.exceptions.PyJWTError as e:
        raise HTTPException(
            status_code=403,
            detail="Invalid Cloudflare authorization token: Invalid signature",
        ) from e
