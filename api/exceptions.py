"""Domain exceptions raised by the operations layer.

Operations are decoupled from the FastAPI/HTTP stack, so they raise these
instead of `HTTPException`. `api.exception_handlers` maps any `AccessException`
to the RFC 9457 problem-detail response via its `status_code`. Callers outside
an HTTP request (the syncer, CLI commands) can catch `AccessException` directly.
"""

from __future__ import annotations


class AccessException(Exception):
    """Base class for operation-layer domain errors.

    `status_code` is the HTTP status the exception handler emits when this
    propagates out of an API request. Subclasses pin a default; the constructor
    can override it for one-off cases.
    """

    status_code: int = 400

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail)


class ConflictError(AccessException):
    """The resource's current state conflicts with the requested action
    (e.g. a request that has already been resolved)."""

    status_code = 409


class ResourceGoneError(AccessException):
    """A resource the action depends on no longer exists (soft-deleted)."""

    status_code = 410


class InvalidRequestError(AccessException):
    """The action is invalid for the resource's current configuration
    (e.g. a group that is not managed by Access)."""

    status_code = 400
