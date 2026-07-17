import asyncio
from types import SimpleNamespace
from typing import Any

from api.syncer import _prefetch_group_okta_lists


def _groups(n: int) -> list[Any]:
    return [SimpleNamespace(id=f"g{i}") for i in range(n)]


async def test_prefetch_bounds_concurrency_and_yields_all() -> None:
    """The window holds at most ``batch_size`` fetches in flight, fills to it, and
    every group is fetched exactly once and paired with its own result."""
    groups = _groups(25)
    active = 0
    peak = 0

    async def fetch(group_id: str) -> list[str]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)  # let the rest of the window start before this one finishes
        active -= 1
        return [group_id]

    out: dict[str, Any] = {}
    async for group, result in _prefetch_group_okta_lists(groups, fetch, 10):
        out[group.id] = result

    assert len(out) == 25
    assert all(out[f"g{i}"] == [f"g{i}"] for i in range(25))
    # Never exceeds the window, and the window actually fills (real concurrency).
    assert peak <= 10
    assert peak == 10


async def test_prefetch_captures_fetch_exceptions_without_aborting() -> None:
    """A failing fetch surfaces its exception as the paired value instead of
    aborting the rest of the run."""
    groups = _groups(3)
    boom = RuntimeError("boom")

    async def fetch(group_id: str) -> list[str]:
        if group_id == "g1":
            raise boom
        return [group_id]

    out: dict[str, Any] = {}
    async for group, result in _prefetch_group_okta_lists(groups, fetch, 10):
        out[group.id] = result

    assert len(out) == 3
    assert out["g0"] == ["g0"]
    assert out["g2"] == ["g2"]
    assert out["g1"] is boom


async def test_prefetch_handles_empty_group_list() -> None:
    """An empty group list yields nothing and never calls fetch."""

    async def fetch(group_id: str) -> list[str]:
        raise AssertionError("fetch should not be called for an empty group list")

    out = [pair async for pair in _prefetch_group_okta_lists([], fetch, 10)]
    assert out == []
