"""
FastAPI authentication middleware for opt-out authentication.
All routes are protected by default unless explicitly marked as public.
"""

from typing import Optional, Set

from fastapi import Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from api_v2.auth.authentication import get_cloudflare_auth, get_oidc_auth
from api_v2.config import settings
from api_v2.database import get_session_local
from api_v2.models import OktaUser

# Routes that don't require authentication
PUBLIC_ROUTES: Set[str] = {
    "/api/v2/healthz",
    "/api/v2/healthz/auth",
}

# Prefixes for paths that should be public
PUBLIC_PREFIXES = []


class AuthenticationResult:
    def __init__(self, user_id: str, user: Optional[OktaUser]):
        self.user_id = user_id
        self.user = user

    def __bool__(self):
        return self.user_id is not None


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces authentication on all routes except those explicitly marked as public.
    This implements an opt-out approach where authentication is required by default.
    """

    async def dispatch(self, request: Request, call_next):
        # Check if the route is public
        if self._is_public_route(request):
            return await call_next(request)

        # Try to authenticate the user
        auth_result = await self._authenticate_user(request)

        if not auth_result:
            # No valid authentication found
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Store the authenticated user in request state for use in endpoints
        request.state.user_id = auth_result.user_id
        request.state.user = auth_result.user

        # Proceed with the request
        response = await call_next(request)
        return response

    def _is_public_route(self, request: Request) -> bool:
        """
        Check if a route should be public (no authentication required).

        Args:
            request: The FastAPI request object

        Returns:
            True if the route is public, False otherwise
        """
        path = request.url.path

        # Check exact matches
        if path in PUBLIC_ROUTES:
            return True

        # Check prefixes
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return True

        # Check if path is root or ends with /
        if path == "/" or path == "":
            return True

        # Check if the matched route's endpoint has the _public attribute
        for route in request.app.routes:
            match, scope = route.matches(request.scope)
            if match == Match.FULL:
                # Check if the endpoint has the _public attribute set by public_route decorator
                if hasattr(route, "endpoint"):
                    endpoint = route.endpoint
                    if hasattr(endpoint, "_public") and endpoint._public:
                        return True
                break

        return False

    async def _authenticate_user(self, request: Request) -> Optional[AuthenticationResult]:
        """
        Authenticate a user based on the request.
        This replicates the logic from get_current_user dependency.

        Args:
            request: The FastAPI request object

        Returns:
            OktaUser if authenticated, None otherwise
        """
        session_local = get_session_local()
        db = session_local()
        try:
            # Development/test mode - bypass authentication
            if settings.env in ("development", "test"):
                test_email = settings.current_okta_user_email
                if not test_email:
                    return None

                current_user = (
                    db.query(OktaUser)
                    .filter(func.lower(OktaUser.email) == func.lower(test_email))
                    .filter(OktaUser.deleted_at.is_(None))
                    .first()
                )
                if current_user:
                    return AuthenticationResult(current_user.id, current_user)
                else:
                    return None

            # Cloudflare Access authentication
            elif settings.cloudflare_team_domain:
                try:
                    cloudflare_auth = get_cloudflare_auth()
                    payload = await cloudflare_auth.verify_cloudflare_request(request)

                    if "email" in payload:
                        current_user = (
                            db.query(OktaUser)
                            .filter(func.lower(OktaUser.email) == func.lower(payload["email"]))
                            .filter(OktaUser.deleted_at.is_(None))
                            .first()
                        )
                        if current_user:
                            return AuthenticationResult(current_user.id, current_user)
                    elif "common_name" in payload:
                        # For cases where we only have common_name, we need to handle this differently
                        return AuthenticationResult(payload["common_name"], None)
                except Exception:
                    # Authentication failed
                    return None

            # OIDC authentication
            elif settings.oidc_client_secrets:
                try:
                    oidc_auth = get_oidc_auth()
                    payload = await oidc_auth.verify_oidc_request(request)

                    # Extract email from token
                    email = payload.get("email")
                    if not email:
                        return None

                    current_user = (
                        db.query(OktaUser)
                        .filter(func.lower(OktaUser.email) == func.lower(email))
                        .filter(OktaUser.deleted_at.is_(None))
                        .first()
                    )
                    if current_user:
                        return AuthenticationResult(current_user.id, current_user)
                    else:
                        return None
                except Exception:
                    # Authentication failed
                    return None

            else:
                # No authentication method configured
                return None

        finally:
            db.close()

        return None


def public_route(func):
    """
    Decorator to mark an endpoint as public (no authentication required).
    Use this for individual endpoints that should bypass authentication.

    Example:
        @router.get("/public-endpoint")
        @public_route
        async def public_endpoint():
            return {"message": "This is public"}
    """
    func._public = True
    return func
