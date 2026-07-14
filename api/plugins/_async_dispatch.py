"""Shared helpers for the async pluggy plugin interface.

`pluggy` never awaits a hook: calling ``pm.hook.<name>(**kwargs)`` just invokes
each registered implementation and collects its return value. For the Access 2.0
async plugin interface the implementations are ``async def``, so the call returns
a list of coroutines that the application runs to completion via
``run_hooks_to_completion``.

A stale *synchronous* implementation of an async hook would return a plain value
instead of a coroutine, which then fails when scheduled as a task.
``verify_async_impls`` turns that into a clear, load-time error naming the
offending plugin — mirroring the fail-fast spirit of
``metrics_reporter._verify_tag_forwarding``.
"""

import asyncio
import inspect
import logging
from typing import Any

import pluggy

logger = logging.getLogger("api")


def verify_async_impls(pm: pluggy.PluginManager, hook_names: tuple[str, ...]) -> None:
    """Raise if any registered impl for an async hook is not a coroutine function.

    Args:
        pm: The plugin manager whose registered implementations to inspect.
        hook_names: The names of the hooks that the application awaits. Hooks that
            are intentionally synchronous (metadata/config/status schemas) or that
            return an async context manager (``batch_metrics``) must be excluded by
            the caller.
    """
    offenders: list[str] = []
    for hook_name in hook_names:
        caller = getattr(pm.hook, hook_name, None)
        if caller is None:
            continue
        for impl in caller.get_hookimpls():
            if not inspect.iscoroutinefunction(impl.function):
                offenders.append(f"{pm.get_name(impl.plugin)}.{hook_name}")
    if offenders:
        raise RuntimeError(
            "The Access 2.0 plugin interface requires these hook implementations to be "
            "declared with 'async def', but they are synchronous: " + "; ".join(sorted(offenders))
        )


async def run_hooks_to_completion(coros: list[Any], *, context: str) -> tuple[list[Any], list[BaseException]]:
    """Run the coroutines a pluggy hook call returned to completion.

    Returns ``(results, exceptions)``: the return values of the implementations
    that succeeded (in input order) and the exceptions raised by those that
    failed. Every failure is also logged at ERROR with its traceback — the app
    does not swallow plugin errors, it surfaces them; a plugin that expects noisy
    failures (e.g. connection timeouts) should catch those itself. The call still
    never raises on a plugin's behalf, so one plugin can't break the operation.

    Uses ``asyncio.wait`` rather than ``asyncio.gather`` deliberately, mirroring
    the fan-out drain in ``api/operations/_fan_out.py`` (discord/access#481):

    - If the awaiting caller is cancelled (e.g. the client disconnected),
      ``gather`` propagates the cancellation to its children and tears down the
      in-flight hook coroutines; ``wait`` leaves them running to completion.
      These hooks fire after the authoritative DB change has committed and only
      do network I/O (notifications, metrics), so an in-flight send must be
      allowed to finish.
    - One implementation failing must never cancel or abandon its siblings —
      ``wait`` runs them all and lets us collect every result and every error.

    Keep this ``wait``; do not "simplify" it back to ``gather``.
    """
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    if not tasks:
        # No implementations registered (or a test double yielded nothing).
        # asyncio.wait() rejects an empty set, so short-circuit.
        return [], []

    await asyncio.wait(tasks)

    results: list[Any] = []
    exceptions: list[BaseException] = []
    for task in tasks:
        exc = task.exception()
        if exc is None:
            results.append(task.result())
        else:
            exceptions.append(exc)
            logger.error("%s: plugin hook raised %r", context, exc, exc_info=exc)
    return results, exceptions
