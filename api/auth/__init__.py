"""Authentication and authorization subsystem.

Cloudflare Access (JWT-based) is the primary auth path. OIDC is a secondary
path preserved for open-source deployments. Permissions helpers and
`Depends` factories live in `permissions.py`.
"""
from api.auth.dependencies import (  # noqa: F401
    CurrentUser,
    CurrentUserId,
    get_current_user,
    get_current_user_id,
)
from api.auth.permissions import (  # noqa: F401
    can_manage_group,
    is_access_admin,
    is_app_owner_group_owner,
    is_group_owner,
    require_access_admin,
    require_access_admin_or_app_creator,
    require_app_owner_or_access_admin_for_app,
    require_group_manager_for_group,
)
