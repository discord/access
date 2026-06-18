"""Pydantic response schemas for the runtime app-config router.

`api/routers/config.py` surfaces deploy-time configuration the frontend
needs at runtime (currently the IdP deep-link templates), read from
`settings` so a single built bundle can target different IdP consoles
per deployment env.
"""

from __future__ import annotations

from pydantic import BaseModel


class IdpConfig(BaseModel):
    """IdP deep-link configuration. Empty strings disable the feature."""

    name: str
    user_url_template: str
    group_url_template: str


class AppConfig(BaseModel):
    idp: IdpConfig
