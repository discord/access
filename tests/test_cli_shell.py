"""The `access shell` REPL: its console, and the namespace it starts with."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from sqlalchemy import select

from api.cli import _AsyncConsole, _shell_namespace, cli


def test_console_evaluates_plain_statements() -> None:
    namespace: dict[str, Any] = {}
    with asyncio.Runner() as runner:
        console = _AsyncConsole(namespace, runner)
        assert console.push("x = 1 + 1") is False

    assert namespace["x"] == 2


def test_console_evaluates_top_level_await() -> None:
    async def answer() -> int:
        return 42

    namespace: dict[str, Any] = {"answer": answer}
    with asyncio.Runner() as runner:
        console = _AsyncConsole(namespace, runner)
        console.push("result = await answer()")

    assert namespace["result"] == 42


def test_console_statements_share_one_context() -> None:
    """Every statement must observe the context the bootstrap established --
    that is what lets `db.session` resolve to the scope `app_context` bound."""
    probe: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="unset")

    async def bind() -> None:
        probe.set("bound")

    async def read() -> str:
        return probe.get()

    namespace: dict[str, Any] = {"bind": bind, "read": read}
    with asyncio.Runner() as runner:
        console = _AsyncConsole(namespace, runner)
        console.push("await bind()")
        console.push("seen = await read()")

    assert namespace["seen"] == "bound"


def test_shell_is_registered_on_the_cli() -> None:
    assert "shell" in cli.commands


def test_shell_namespace_exposes_the_session_models_and_operations() -> None:
    namespace = _shell_namespace()

    assert namespace["db"] is not None
    assert namespace["OktaGroup"] is not None
    assert namespace["ModifyGroupUsers"] is not None
    assert namespace["select"] is select
