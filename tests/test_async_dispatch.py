"""Tests for the async plugin-hook runner (api/plugins/_async_dispatch.py).

These lock in the ``asyncio.wait`` (not ``gather``) behavior: a cancellation of
the awaiting caller must NOT tear down the in-flight hook coroutines, and one
hook failing must not cancel or abandon its siblings. Both assertions fail if
``run_hooks_to_completion`` is "simplified" back to ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio

import pytest

from api.plugins._async_dispatch import run_hooks_to_completion


async def test_empty_returns_empty() -> None:
    results, exceptions = await run_hooks_to_completion([], context="test")
    assert results == []
    assert exceptions == []


async def test_collects_results_and_exceptions_in_input_order() -> None:
    async def ok_a() -> str:
        return "A"

    async def boom() -> str:
        raise RuntimeError("kaboom")

    async def ok_b() -> str:
        return "B"

    results, exceptions = await run_hooks_to_completion([ok_a(), boom(), ok_b()], context="test")

    assert results == ["A", "B"]
    assert len(exceptions) == 1
    assert isinstance(exceptions[0], RuntimeError)


async def test_sibling_failure_does_not_cancel_other_inflight_hooks() -> None:
    """A hook that raises immediately must not cancel a slow sibling mid-flight."""
    completed: list[str] = []

    async def boom() -> None:
        raise RuntimeError("fails fast")

    async def slow() -> str:
        await asyncio.sleep(0.05)
        completed.append("slow")  # only reached if not cancelled
        return "slow"

    results, exceptions = await run_hooks_to_completion([boom(), slow()], context="test")

    assert completed == ["slow"]  # ran to completion despite the sibling's failure
    assert results == ["slow"]
    assert len(exceptions) == 1


async def test_caller_cancellation_does_not_tear_down_inflight_hooks() -> None:
    """Cancelling the awaiting task must leave in-flight hook coroutines running.

    This is the whole reason for ``wait`` over ``gather``: ``gather`` would
    propagate the cancellation to its children, so ``completed`` would stay empty
    and this test would fail.
    """
    completed: list[str] = []
    started = asyncio.Event()

    async def slow() -> None:
        started.set()
        await asyncio.sleep(0.05)
        completed.append("done")  # only reached if the hook was NOT cancelled

    task = asyncio.ensure_future(run_hooks_to_completion([slow()], context="test"))
    await started.wait()  # make sure slow() is genuinely in-flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give the (uncancelled) hook time to finish on the loop.
    await asyncio.sleep(0.2)
    assert completed == ["done"]
