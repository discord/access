"""Per-request context variables.

Operations and audit logging need to know the user-agent, originating IP,
and request id of the current request. `RequestContextMiddleware` sets a
`RequestContext` ContextVar; operations consult `get_request_context()`.
Outside an HTTP request (e.g. the syncer or CLI commands) the context is
None.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    user_agent: Optional[str]
    ip: Optional[str]


_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar("access_request_context", default=None)


def get_request_context() -> Optional[RequestContext]:
    return _ctx.get()


def set_request_context(ctx: Optional[RequestContext]) -> contextvars.Token[Optional[RequestContext]]:
    return _ctx.set(ctx)


def reset_request_context(token: contextvars.Token[Optional[RequestContext]]) -> None:
    _ctx.reset(token)
