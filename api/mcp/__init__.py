"""Embedded MCP (Model Context Protocol) server.

Off by default. ``api.app.create_app`` only constructs / mounts the server
when ``settings.ENABLE_MCP`` is true. See ``api/mcp/server.py`` for the
FastMCP construction, ``api/mcp/auth/`` for the pluggable auth layer, and
``api/mcp/tools.py`` for the tool registrations.

This module deliberately exposes ``__all__`` only — the rest of the
codebase should depend on the helpers in submodules rather than reaching
through ``api.mcp``.
"""

from __future__ import annotations

__all__ = ()
