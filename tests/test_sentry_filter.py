"""Tests for the Sentry `before_send` filter in `api.app`.

The MCP server re-raises tool argument-validation failures as a
`ToolError` wrapping a pydantic `ValidationError`. `ignore_errors` only
matches the top-level type, so those client-error events leak to Sentry.
`_drop_wrapped_validation_errors` closes that gap by walking the cause chain.
"""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from api.app import _drop_wrapped_validation_errors


class _Args(BaseModel):
    group_id_or_name: str


def _validation_error() -> ValidationError:
    try:
        _Args(**{"group_id": "00g1423wlai2EMe85698"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


def _hint(exc: BaseException) -> dict:
    return {"exc_info": (type(exc), exc, exc.__traceback__)}


def test_drops_tool_error_wrapping_validation_error() -> None:
    """The real production case: a client passes a bad tool argument."""
    try:
        raise ToolError("Error executing tool get_group") from _validation_error()
    except ToolError as exc:
        assert _drop_wrapped_validation_errors({"event": True}, _hint(exc)) is None


def test_drops_bare_validation_error() -> None:
    assert _drop_wrapped_validation_errors({"event": True}, _hint(_validation_error())) is None


def test_keeps_generic_exception() -> None:
    event = {"event": True}
    assert _drop_wrapped_validation_errors(event, _hint(RuntimeError("boom"))) is event


def test_keeps_tool_error_wrapping_real_bug() -> None:
    """A genuine server-side failure inside a tool must still alert."""
    event = {"event": True}
    try:
        raise ToolError("Error executing tool get_group") from RuntimeError("db down")
    except ToolError as exc:
        assert _drop_wrapped_validation_errors(event, _hint(exc)) is event


def test_keeps_event_when_no_exc_info() -> None:
    event = {"event": True}
    assert _drop_wrapped_validation_errors(event, {}) is event
    assert _drop_wrapped_validation_errors(event, None) is event  # type: ignore[arg-type]


def test_cause_cycle_terminates() -> None:
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    event = {"event": True}
    assert _drop_wrapped_validation_errors(event, _hint(a)) is event
