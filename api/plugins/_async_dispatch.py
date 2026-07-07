"""Shared helpers for the async pluggy plugin interface (TODO 18).

`pluggy` never awaits a hook: calling ``pm.hook.<name>(**kwargs)`` just invokes
each registered implementation and collects its return value. For the Access 2.0
async plugin interface the implementations are ``async def``, so the call returns
a list of coroutines that the application then awaits with ``asyncio.gather``.

A stale *synchronous* implementation of an async hook would return a plain value
instead of a coroutine, and ``asyncio.gather(*[value, ...])`` fails with an
opaque ``TypeError``. ``verify_async_impls`` turns that into a clear, load-time
error naming the offending plugin — mirroring the fail-fast spirit of
``metrics_reporter._verify_tag_forwarding``.
"""

import inspect

import pluggy


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
