from __future__ import annotations

from typing import Awaitable, Callable, Optional
from unittest.mock import MagicMock

import pytest

import api.plugins.notifications as notifications_module
from api.plugins import notifications

# hook name -> (metric name, tags) — mirrors notifications._SENT_METRICS. The
# "sent" counter is recorded by send_notification after the async hook fans out
# successfully (the async replacement for the old @hookimpl(wrapper=True) set).
HOOKS: list[tuple[str, str, Optional[dict[str, str]]]] = [
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


class _FakeHook:
    """Stand-in for pluggy's HookRelay: every hook caller returns a list holding
    a single freshly-built coroutine, mimicking one registered async hookimpl."""

    def __init__(self, behavior: Callable[[], Awaitable[None]]) -> None:
        self._behavior = behavior

    def __getattr__(self, name: str) -> Callable[..., list[Awaitable[None]]]:
        def caller(**kwargs: object) -> list[Awaitable[None]]:
            return [self._behavior()]

        return caller


def _install_hook(monkeypatch: pytest.MonkeyPatch, behavior: Callable[[], Awaitable[None]]) -> None:
    monkeypatch.setattr(notifications_module, "get_notification_hook", lambda: _FakeHook(behavior))


@pytest.fixture
def fake_metrics(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(notifications_module, "get_metrics_reporter_hook", lambda: fake)
    return fake


@pytest.mark.parametrize("hook_name,metric,tags", HOOKS)
async def test_send_notification_emits_counter_on_success(
    fake_metrics: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    hook_name: str,
    metric: str,
    tags: Optional[dict[str, str]],
) -> None:
    async def ok() -> None:
        return None

    _install_hook(monkeypatch, ok)
    await notifications.send_notification(hook_name)
    fake_metrics.record_counter.assert_called_once_with(metric_name=metric, value=1, tags=tags)


@pytest.mark.parametrize("hook_name", [h[0] for h in HOOKS])
async def test_send_notification_does_not_emit_on_failure(
    fake_metrics: MagicMock, monkeypatch: pytest.MonkeyPatch, hook_name: str
) -> None:
    async def boom() -> None:
        raise RuntimeError("inner plugin blew up")

    _install_hook(monkeypatch, boom)
    # The plugin failure is swallowed (logged, not raised) and no counter emitted.
    await notifications.send_notification(hook_name)
    fake_metrics.record_counter.assert_not_called()


async def test_metric_emit_failure_does_not_break_send(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom_hook() -> object:
        raise RuntimeError("metrics_reporter unavailable")

    monkeypatch.setattr(notifications_module, "get_metrics_reporter_hook", boom_hook)

    async def ok() -> None:
        return None

    _install_hook(monkeypatch, ok)
    # A failure recording the metric must not propagate out of send_notification.
    await notifications.send_notification("access_request_created")
