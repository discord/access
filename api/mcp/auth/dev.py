"""Local-development MCP auth provider.

Activates only when ``settings.ENV`` is ``development`` or ``test``. In
those modes it resolves the user identified by
``settings.CURRENT_OKTA_USER_EMAIL`` — the same shortcut the REST path
takes in ``api/auth/dependencies.py::get_current_user_id`` — and grants
the full v1 scope set so a developer running locally can exercise both
read and write tools without needing to fake a Cloudflare JWT.

This provider is always registered alongside the Cloudflare default. The
``ENV`` guard means it returns ``None`` (defers) in any production-style
environment, so registering it unconditionally is safe. Locally, the
Cloudflare provider opts out via its own ``CLOUDFLARE_TEAM_DOMAIN`` check,
so the two never compete in practice.

Operators running MCP under a real (non-CF) auth model in production
still write their own provider and register it via the
``access_mcp_auth`` setuptools entry point — this file is for the local
dev shell only.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func
from starlette.types import Scope

from api.config import settings
from api.extensions import db as _db_shim
from api.mcp.auth import ALL_V1_SCOPES, MCPIdentity
from api.models import OktaUser
from api.plugins.mcp_auth import hookimpl

logger = logging.getLogger(__name__)


@hookimpl
def mcp_resolve_identity(scope: Scope) -> Optional[MCPIdentity]:
    if settings.ENV not in ("development", "test"):
        return None

    email = settings.CURRENT_OKTA_USER_EMAIL
    if not email or email == "Unauthenticated":
        return None

    db = _db_shim.session
    user = (
        db.query(OktaUser)
        .filter(func.lower(OktaUser.email) == func.lower(email))
        .filter(OktaUser.deleted_at.is_(None))
        .first()
    )
    if user is None:
        logger.warning(
            f"Dev MCP auth provider: CURRENT_OKTA_USER_EMAIL={email!r} has no matching active OktaUser; deferring"
        )
        return None

    return MCPIdentity(user_id=user.id, scopes=ALL_V1_SCOPES)
