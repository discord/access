from __future__ import annotations

import inspect
from typing import Any, Callable, Optional
from unittest.mock import MagicMock

import pytest

import api.plugins.notifications as notifications_module
from api.plugins import notifications

WRAPPERS: list[tuple[str, str, Optional[dict[str, str]]]] = [
    ("access_request_created", "notifications.access_request_created.sent", None),
    ("access_request_completed", "notifications.access_request_completed.sent", None),
    ("access_expiring_user", "notifications.expiring_access.sent", {"kind": "user"}),
    ("access_expiring_owner", "notifications.expiring_access.sent", {"kind": "owner"}),
    ("access_expiring_role_owner", "notifications.expiring_access.sent", {"kind": "role_owner"}),
    ("access_role_request_created", "notifications.role_request_created.sent", None),
    ("access_role_request_completed", "notifications.role_request_completed.sent", None),
    ("access_group_request_created", "notifications.group_request_created.sent", None),
    ("access_group_request_completed", "notifications.group_request_completed.sent", None),
]


def _kwargs_for(wrapper_fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(wrapper_fn)
    return {name: True if name == "notify_requester" else None for name in sig.parameters}


def _drive_success(wrapper_fn: Callable[..., Any]) -> None:
    gen = wrapper_fn(**_kwargs_for(wrapper_fn))
    next(gen)
    with pytest.raises(StopIteration):
        gen.send(None)


def _drive_failure(wrapper_fn: Callable[..., Any], exc: BaseException) -> None:
    gen = wrapper_fn(**_kwargs_for(wrapper_fn))
    next(gen)
    with pytest.raises(StopIteration):
        gen.throw(exc)


@pytest.fixture
def fake_hook(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(notifications_module, "get_metrics_reporter_hook", lambda: fake)
    return fake


@pytest.mark.parametrize("wrapper_name,metric,tags", WRAPPERS)
def test_wrapper_emits_counter_on_success(
    fake_hook: MagicMock, wrapper_name: str, metric: str, tags: Optional[dict[str, str]]
) -> None:
    _drive_success(getattr(notifications, wrapper_name))
    fake_hook.record_counter.assert_called_once_with(metric_name=metric, value=1, tags=tags)


@pytest.mark.parametrize("wrapper_name", [w[0] for w in WRAPPERS])
def test_wrapper_does_not_emit_counter_on_failure(fake_hook: MagicMock, wrapper_name: str) -> None:
    _drive_failure(getattr(notifications, wrapper_name), RuntimeError("inner plugin blew up"))
    fake_hook.record_counter.assert_not_called()


def test_metric_emit_failure_does_not_break_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("metrics_reporter unavailable")

    monkeypatch.setattr(notifications_module, "get_metrics_reporter_hook", boom)
    _drive_success(notifications.access_request_created)
