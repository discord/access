"""Deferring the Okta/notification fan-out to a post-response BackgroundTask (TODO 10).

The fan-out operations hand their Okta + notification tasks to a request-scoped
collector via `defer_or_drain_fan_out`; the `defer_fan_out` router dependency
drains that collector from a FastAPI `BackgroundTask` after the response, so the
HTTP response returns as soon as the local DB state commits. Outside an opted-in
request the batch is drained inline.

The crux is notification correctness: the deferred drain runs after the router's
`db.expire_all()` (and, in prod, session teardown), so the operations `expunge`
the notification payload first — without that, the async hook's synchronous
attribute reads would raise on the expired/dead session and the notification
would be silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI
from httpx import AsyncClient
from pytest_mock import MockerFixture

from api.extensions import Db
from api.models import AccessRequest, AccessRequestStatus, OktaGroup, OktaUser
from api.operations._fan_out import (
    _deferred_fan_out,
    begin_deferred_fan_out,
    defer_or_drain_fan_out,
    detach_for_deferred_fan_out,
    end_deferred_fan_out,
    run_deferred_fan_out,
)
from api.plugins.notifications import get_notification_hook
from api.routers._fan_out import defer_fan_out
from api.services import okta


# --- defer_or_drain_fan_out: defer vs. drain-inline vs. no-op -----------------


async def test_defer_or_drain_appends_without_awaiting_when_collector_set() -> None:
    ran: list[str] = []

    async def _work() -> None:
        ran.append("ran")

    task = asyncio.create_task(_work())
    collected, token = begin_deferred_fan_out()
    try:
        await defer_or_drain_fan_out([task], "UnitOp ctx-1")
        # Deferred: the batch is collected, not drained — the task hasn't run.
        assert collected == [([task], "UnitOp ctx-1")]
        assert not task.done()
    finally:
        end_deferred_fan_out(token)

    # The deferred drain runs it later.
    await run_deferred_fan_out(collected)
    assert task.done()
    assert ran == ["ran"]


async def test_defer_or_drain_drains_inline_when_no_collector() -> None:
    ran: list[str] = []

    async def _work() -> None:
        ran.append("ran")

    task = asyncio.create_task(_work())
    # No collector bound (CLI/syncer/direct-execute): drain inline.
    assert _deferred_fan_out.get() is None
    await defer_or_drain_fan_out([task], "UnitOp ctx-1")
    assert task.done()
    assert ran == ["ran"]


async def test_defer_or_drain_empty_is_noop() -> None:
    collected, token = begin_deferred_fan_out()
    try:
        await defer_or_drain_fan_out([], "UnitOp ctx-1")
        assert collected == []
    finally:
        end_deferred_fan_out(token)


async def test_run_deferred_fan_out_logs_failures_at_error(caplog: pytest.LogCaptureFixture) -> None:
    async def _boom() -> None:
        raise RuntimeError("deferred task exploded")

    task = asyncio.create_task(_boom())
    with caplog.at_level(logging.ERROR, logger="api"):
        await run_deferred_fan_out([([task], "DeferOp ctx-1")])

    recs = [r for r in caplog.records if "DeferOp ctx-1" in r.getMessage()]
    assert len(recs) == 1
    assert recs[0].levelno == logging.ERROR
    assert recs[0].exc_info is not None
    assert "deferred task exploded" in str(recs[0].exc_info[1])


# --- defer_fan_out dependency -------------------------------------------------


async def test_defer_fan_out_registers_background_task_and_manages_contextvar() -> None:
    bg = BackgroundTasks()
    agen = defer_fan_out(bg)

    await agen.__anext__()  # run up to the yield (the "request")
    # A drain is registered and the collector is bound while the request runs.
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func.__qualname__ == run_deferred_fan_out.__qualname__
    assert _deferred_fan_out.get() is not None

    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()  # finalize (post-response teardown)
    # The collector is unbound afterwards.
    assert _deferred_fan_out.get() is None


async def test_defer_fan_out_drains_inline_on_endpoint_error(caplog: pytest.LogCaptureFixture) -> None:
    ran: list[str] = []

    async def _work() -> None:
        ran.append("ran")

    bg = BackgroundTasks()
    agen = defer_fan_out(bg)
    await agen.__anext__()

    # An operation defers a batch during the (about-to-fail) request.
    task = asyncio.create_task(_work())
    await defer_or_drain_fan_out([task], "UnitOp ctx-1")
    assert not task.done()

    # The endpoint raises: the error response won't carry our BackgroundTask, so
    # the dependency drains inline before re-raising.
    with caplog.at_level(logging.ERROR, logger="api"):
        with pytest.raises(ValueError):
            await agen.athrow(ValueError("endpoint boom"))

    assert task.done()
    assert ran == ["ran"]
    assert _deferred_fan_out.get() is None


# --- detach helper ------------------------------------------------------------


async def test_detach_for_deferred_fan_out_is_noop_when_not_deferring(db: Db, okta_group: OktaGroup) -> None:
    db.session.add(okta_group)
    await db.session.commit()
    assert _deferred_fan_out.get() is None  # inline path — no collector bound

    detach_for_deferred_fan_out(db.session, [okta_group])
    # Left attached: the inline drain runs against the live session, and the
    # caller (CLI/direct-execute) may still mutate the object.
    assert okta_group in db.session


async def test_detach_for_deferred_fan_out_is_selective_and_idempotent(db: Db, okta_group: OktaGroup) -> None:
    db.session.add(okta_group)
    await db.session.commit()
    assert okta_group in db.session

    collected, token = begin_deferred_fan_out()  # deferring → detach is active
    try:
        # None and objects already absent from the session are tolerated.
        detach_for_deferred_fan_out(db.session, [None, okta_group])
        assert okta_group not in db.session

        # Re-detaching an already-detached object must not raise (expunge would).
        detach_for_deferred_fan_out(db.session, [okta_group])
    finally:
        end_deferred_fan_out(token)

    # A loaded attribute is still readable on the detached instance.
    assert okta_group.name is not None


# --- crux: deferred notification reads ORM state after the router expires it ---


async def test_deferred_notification_reads_orm_after_router_expire_all(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_request: AccessRequest,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    """Approving over HTTP defers the completion notification; it must still read
    the request/group/requester attributes even though the router calls
    `db.expire_all()` before the deferred drain runs. This fails without the
    operations expunging the notification payload."""
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    await db.session.commit()

    # Okta sync is irrelevant here; keep it a no-op.
    mocker.patch.object(okta, "add_user_to_group")
    mocker.patch.object(okta, "add_owner_to_group")
    mocker.patch.object(okta, "remove_user_from_group")
    mocker.patch.object(okta, "remove_owner_from_group")

    # A notification hook that READS a broad set of column attributes off every
    # object it is handed — the request, its group, the requester, and each
    # approver — mirroring what a real notifier (Slack/email) does. This is
    # exactly what a missing or incomplete expunge would break: reading an
    # unloaded/expired attribute on a detached instance raises
    # DetachedInstanceError/MissingGreenlet, which send_notification swallows
    # into a silent drop.
    seen: list[dict[str, Any]] = []
    hook = get_notification_hook()

    def _completed(**kwargs: Any) -> list[Any]:
        async def _impl() -> None:
            ar = kwargs["access_request"]
            grp = kwargs["group"]
            req = kwargs["requester"]
            approvers = kwargs["approvers"]
            seen.append(
                {
                    "ar_id": ar.id,
                    "status": ar.status,
                    "resolution_reason": ar.resolution_reason,
                    "request_reason": ar.request_reason,
                    "request_ownership": ar.request_ownership,
                    "ar_requester_id": ar.requester_user_id,
                    "ar_group_id": ar.requested_group_id,
                    "group_id": grp.id,
                    "group_name": grp.name,
                    "group_type": grp.type,
                    "requester_id": req.id,
                    "requester_email": req.email,
                    "requester_name": req.display_name,
                    # Iterating the approvers set and reading each one's columns
                    # exercises detached reads on every OktaUser in the payload.
                    "approver_emails": sorted(a.email for a in approvers),
                }
            )

        return [_impl()]

    mocker.patch.object(hook, "access_request_completed", side_effect=_completed)

    url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    rep = await client.put(url, json={"approved": True, "reason": "approved for test"})

    assert rep.status_code == 200
    # The deferred notification ran and read every attribute off the expunged,
    # detached instances without a live session (no DetachedInstanceError).
    assert len(seen) == 1
    payload = seen[0]
    assert payload["ar_id"] == access_request.id
    assert payload["status"] == AccessRequestStatus.APPROVED
    assert payload["resolution_reason"] == "approved for test"
    assert payload["request_ownership"] is False
    assert payload["ar_requester_id"] == user.id
    assert payload["ar_group_id"] == okta_group.id
    assert payload["group_id"] == okta_group.id
    assert payload["group_name"] == okta_group.name
    assert payload["group_type"] == "okta_group"
    assert payload["requester_id"] == user.id
    assert payload["requester_email"] == user.email
    # approvers is a (possibly empty) list of detached OktaUser snapshots; the
    # reads above must not raise regardless of how many there are.
    assert isinstance(payload["approver_emails"], list)


async def test_deferred_notification_on_reject_reads_orm_after_expire_all(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    mocker: MockerFixture,
    access_request: AccessRequest,
    okta_group: OktaGroup,
    user: OktaUser,
    url_for: Any,
) -> None:
    """Rejecting over HTTP also defers its completion notification — reject used
    to dispatch it inline (blocking the response). The hook must still read the
    expunged payload after the router's `db.expire_all()`/teardown."""
    db.session.add(user)
    db.session.add(okta_group)
    await db.session.commit()
    access_request.requested_group_id = okta_group.id
    access_request.requester_user_id = user.id
    db.session.add(access_request)
    await db.session.commit()

    seen: list[dict[str, Any]] = []
    hook = get_notification_hook()

    def _completed(**kwargs: Any) -> list[Any]:
        async def _impl() -> None:
            ar = kwargs["access_request"]
            grp = kwargs["group"]
            req = kwargs["requester"]
            seen.append({"status": ar.status, "group_name": grp.name, "requester_email": req.email})

        return [_impl()]

    mocker.patch.object(hook, "access_request_completed", side_effect=_completed)

    url = url_for("api-access-requests.access_request_by_id", access_request_id=access_request.id)
    # Default current user (an access admin) rejects the request.
    rep = await client.put(url, json={"approved": False, "reason": "not needed"})

    assert rep.status_code == 200
    assert len(seen) == 1
    assert seen[0]["status"] == AccessRequestStatus.REJECTED
    assert seen[0]["group_name"] == okta_group.name
    assert seen[0]["requester_email"] == user.email
