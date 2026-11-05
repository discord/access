"""Per-request context variables.

Operations and audit logging need to know the user-agent, originating IP,
and request id of the current request. `RequestContextMiddleware` sets a
`RequestContext` ContextVar; operations consult `get_request_context()`.
Outside an HTTP request (e.g. the syncer or CLI commands) the context is
None, and the process-level source (`server`, `cli:<command>`) set at logging
configuration time is the closest attribution available.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Literal

# "web" = a request through the FastAPI HTTP API. "mcp" = a request that
# arrived through the embedded MCP server. Operations log this in audit
# entries so incident response can distinguish LLM-agent actions from
# hand-driven ones.
RequestSource = Literal["web", "mcp"]


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    user_agent: str | None
    ip: str | None
    source: RequestSource = "web"


_ctx: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("access_request_context", default=None)


def get_request_context() -> RequestContext | None:
    return _ctx.get()


def set_request_context(ctx: RequestContext | None) -> contextvars.Token[RequestContext | None]:
    return _ctx.set(ctx)


def reset_request_context(token: contextvars.Token[RequestContext | None]) -> None:
    _ctx.reset(token)


# Set once per process by `api.app._configure_logging`. Module-level rather
# than a ContextVar: it describes the process, not a task.
_process_source: str | None = None


def get_process_source() -> str | None:
    """Return the entrypoint this process runs as (`server`, `cli:<command>`), or None if unset."""
    return _process_source


def set_process_source(source: str | None) -> None:
    """Record the entrypoint this process runs as, for log and audit attribution."""
    global _process_source
    _process_source = source
