"""Runtime app-config router.

Surfaces deploy-time configuration the frontend reads at runtime — currently
the IdP deep-link templates rendered as the "Open in IdP" button on user and
group pages. Reading these at runtime (rather than baking them into the Vite
build) lets one built bundle target different IdP consoles per deployment env.

Auth-gated by the global `require_authenticated` dependency in
`api.app.create_app`; the response carries no secrets (URL templates only).
"""

from __future__ import annotations

from fastapi import APIRouter

from api.config import settings
from api.schemas import AppConfig, IdpConfig

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", name="app_config")
def get_app_config() -> AppConfig:
    return AppConfig(
        idp=IdpConfig(
            name=settings.IDP_NAME,
            user_url_template=settings.IDP_USER_URL_TEMPLATE,
            group_url_template=settings.IDP_GROUP_URL_TEMPLATE,
        )
    )
