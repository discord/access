"""Architecture checks that turn rules from `.claude/CLAUDE.md` into failing tests.

- Every POST, PUT, PATCH, and DELETE route resolves `CurrentUserId` through its
  own dependency tree, and none of them can be reached through the
  `require_authenticated` short-circuit allowlist.
- Every operation class under `api/operations/` with an `execute()` method
  emits to the `access.audit` logger.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

import api.app as app_module
from api.auth.dependencies import (
    AUTH_ALLOWLIST_PREFIXES,
    get_current_user_id,
    require_authenticated,
    should_allow_unauthenticated_requests,
)
from api.config import settings

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

REPO_ROOT = Path(__file__).resolve().parent.parent
OPERATIONS_DIR = REPO_ROOT / "api" / "operations"
AUDIT_LOGGER_NAME = "access.audit"


# ---------------------------------------------------------------------------
# Mutating routes require auth
# ---------------------------------------------------------------------------


@pytest.fixture
def full_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[FastAPI]:
    """Build the app with every optional route source switched on.

    MCP, OIDC, and the SPA catch-all each register routes only when
    configured, so the shared `app` fixture would never show them to the
    route walk.
    """
    from api.mcp import server as mcp_server

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html></html>")
    monkeypatch.setattr(app_module, "BUILD_DIR", build_dir)
    monkeypatch.setattr(settings, "ENABLE_MCP", True)
    monkeypatch.setattr(settings, "SECRET_KEY", "architecture-test-secret-key-32-bytes!!")
    monkeypatch.setattr(
        settings,
        "OIDC_CLIENT_SECRETS",
        {"web": {"client_id": "test-client", "client_secret": "test-secret", "issuer": "https://idp.test"}},
    )
    # A private OAuth registry keeps this stub client out of the module-level
    # one that the OIDC tests register against.
    monkeypatch.setattr("api.auth.oidc.oauth", OAuth())
    monkeypatch.setattr(mcp_server, "_mcp_server", None)
    try:
        yield app_module.create_app(testing=True)
    finally:
        mcp_server._mcp_server = None


def _route_methods(route: RouteContext) -> frozenset[str]:
    """Return the HTTP methods a route answers; `None` on a Starlette route means all of them."""
    methods = route.methods
    return MUTATING_METHODS if methods is None else frozenset(methods)


def _dependency_calls(dependant: Dependant) -> Iterator[Any]:
    """Yield the callable of every dependency in a route's tree, depth first."""
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def _sample_path(path_format: str) -> str:
    """Fill each `{param}` in a route path with a placeholder segment."""
    return re.sub(r"{[^}]+}", "x", path_format)


def _reaches_auth_allowlist(route: RouteContext) -> bool:
    """Return whether some path this route matches skips `require_authenticated`.

    Checks the route's own path with its parameters filled in, then asks the
    route's regex whether it would also match each allowlist entry, which is
    how a parameterized route such as `/{rest:path}` reaches the allowlist.
    """
    if should_allow_unauthenticated_requests(_sample_path(route.path or "")):
        return True
    path_regex = getattr(route, "path_regex", None)
    if path_regex is None:
        return False
    probes = [probe for prefix in AUTH_ALLOWLIST_PREFIXES for probe in (prefix.rstrip("/"), prefix + "x")]
    return any(path_regex.match(probe) and should_allow_unauthenticated_requests(probe) for probe in probes)


def _is_mcp_route_behind_its_middleware(app: FastAPI, route: RouteContext) -> bool:
    """Return whether `route` is the MCP transport and `MCPAuthMiddleware` is installed.

    The MCP route is a raw ASGI handler outside FastAPI's dependency system,
    so `MCPAuthMiddleware` authenticates it instead of `CurrentUserId`.
    """
    from api.mcp.server import MCP_PATH, MCPAuthMiddleware

    return route.path == MCP_PATH and any(m.cls is MCPAuthMiddleware for m in app.user_middleware)


def test_mutating_routes_require_current_user_id(full_app: FastAPI) -> None:
    """Every mutating route resolves `CurrentUserId` and sits outside the auth allowlist."""
    violations: list[str] = []
    # `app.routes` holds one wrapper per `include_router` call; the route
    # contexts are the individual routes with the include-time dependencies,
    # including the app-wide `require_authenticated`, merged in.
    for route in iter_route_contexts(full_app.routes):
        methods = _route_methods(route) & MUTATING_METHODS
        if not methods:
            continue
        label = f"{','.join(sorted(methods))} {route.path or repr(route.original_route)}"

        if _reaches_auth_allowlist(route):
            violations.append(
                f"{label}: matches the require_authenticated allowlist {AUTH_ALLOWLIST_PREFIXES}, so it runs "
                "without authentication. Mutating routes must never be reachable through the allowlist."
            )

        if not isinstance(route.original_route, APIRoute):
            if not _is_mcp_route_behind_its_middleware(full_app, route):
                violations.append(
                    f"{label}: is a {type(route.original_route).__name__}, not a FastAPI APIRoute, so it bypasses the "
                    "dependency system and nothing resolves CurrentUserId for it."
                )
            continue

        calls = list(_dependency_calls(route.dependant))
        if get_current_user_id not in calls:
            violations.append(
                f"{label} ({route.name}): does not depend on CurrentUserId, directly or through a nested "
                "Depends such as require_access_admin. All POST, PUT, PATCH, and DELETE endpoints must "
                "require authentication."
            )
        if require_authenticated not in calls:
            violations.append(
                f"{label} ({route.name}): is missing the app-wide require_authenticated dependency; "
                "register it through include_router rather than appending it to app.routes."
            )

    assert not violations, "Mutating-route auth rule violated:\n  " + "\n  ".join(violations)


# ---------------------------------------------------------------------------
# Operations write audit logs
# ---------------------------------------------------------------------------


# Operations that do not emit to `access.audit` themselves. Each entry names
# the reason, and `test_operation_audit_exemptions_are_current` fails once an
# entry stops being true, so this list can only shrink.
#
# `audited_by` lists operation classes whose own audit log covers this one's
# writes; the test checks that this class calls each of them and that each
# emits directly.
@dataclass(frozen=True)
class AuditExemption:
    reason: str
    audited_by: tuple[str, ...] = ()


AUDIT_EXEMPTIONS: dict[str, AuditExemption] = {
    "PruneRedundantDirectAccess": AuditExemption(
        reason="Every membership it ends goes through ModifyGroupUsers, which logs GROUP_MODIFY_USER.",
        audited_by=("ModifyGroupUsers",),
    ),
    "ModifyGroupPluginData": AuditExemption(
        reason=(
            "Its one HTTP caller, the group PUT handler, logs GROUP_MODIFY_PLUGIN with the before and "
            "after plugin data."
        ),
    ),
    "ModifyGroupsTimeLimit": AuditExemption(
        reason=(
            "Runs only inside a tag change, which its callers log: ModifyGroupTags (GROUP_MODIFY_TAG), "
            "ModifyAppTags (APP_MODIFY_TAG), ModifyGroupType (GROUP_MODIFY_TYPE), and the tag PUT "
            "handler (TAG_MODIFY)."
        ),
    ),
}


@dataclass(frozen=True)
class OperationClass:
    name: str
    path: Path
    emits_audit_log: bool
    instantiates: frozenset[str]


def _is_audit_get_logger(node: ast.AST) -> bool:
    """Return whether `node` is a `getLogger("access.audit")` call, qualified or not."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    first = node.args[0]
    return name == "getLogger" and isinstance(first, ast.Constant) and first.value == AUDIT_LOGGER_NAME


def _module_audit_logger_names(tree: ast.Module) -> set[str]:
    """Return module-level names bound to `getLogger("access.audit")`."""
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and _is_audit_get_logger(stmt.value):
            names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    return names


def _emits_audit_log(cls: ast.ClassDef, logger_names: set[str]) -> bool:
    """Return whether the class body calls a method on the `access.audit` logger."""
    for node in ast.walk(cls):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        if _is_audit_get_logger(receiver):
            return True
        if isinstance(receiver, ast.Name) and receiver.id in logger_names:
            return True
    return False


def _operation_classes() -> dict[str, OperationClass]:
    """Parse `api/operations/*.py` and return each class that defines `execute()`.

    The `constraints` subpackage is left out: its classes validate a request
    and write nothing.
    """
    found: dict[str, OperationClass] = {}
    for path in sorted(OPERATIONS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        logger_names = _module_audit_logger_names(tree)
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            has_execute = any(
                isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "execute" for m in cls.body
            )
            if not has_execute:
                continue
            instantiates = frozenset(
                n.func.id for n in ast.walk(cls) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            )
            found[cls.name] = OperationClass(
                name=cls.name,
                path=path.relative_to(REPO_ROOT),
                emits_audit_log=_emits_audit_log(cls, logger_names),
                instantiates=instantiates,
            )
    return found


def test_operations_emit_audit_logs() -> None:
    """Every operation's class body logs to `access.audit` unless `AUDIT_EXEMPTIONS` explains why not."""
    operations = _operation_classes()
    assert operations, f"found no operation classes under {OPERATIONS_DIR}; has the package moved?"
    violations = [
        f"{op.name} ({op.path}): defines execute() but never calls logging.getLogger({AUDIT_LOGGER_NAME!r}). "
        "Every operation must emit a structured audit log (AuditLogSchema plus an EventType)."
        for op in operations.values()
        if not op.emits_audit_log and op.name not in AUDIT_EXEMPTIONS
    ]
    assert not violations, "Operation audit-log rule violated:\n  " + "\n  ".join(violations)


def test_operation_audit_exemptions_are_current() -> None:
    """Each `AUDIT_EXEMPTIONS` entry names an unaudited operation, and its `audited_by` delegates hold."""
    operations = _operation_classes()
    stale: list[str] = []
    for name, exemption in AUDIT_EXEMPTIONS.items():
        op = operations.get(name)
        if op is None:
            stale.append(f"{name}: no longer an operation class under {OPERATIONS_DIR.relative_to(REPO_ROOT)}.")
            continue
        if op.emits_audit_log:
            stale.append(f"{name}: now emits to {AUDIT_LOGGER_NAME!r}; remove its AUDIT_EXEMPTIONS entry.")
        for delegate in exemption.audited_by:
            if delegate not in op.instantiates:
                stale.append(f"{name}: is said to be audited by {delegate}, but never calls it.")
            elif not (delegate in operations and operations[delegate].emits_audit_log):
                stale.append(f"{name}: is said to be audited by {delegate}, which does not emit an audit log.")
    assert not stale, "AUDIT_EXEMPTIONS is out of date:\n  " + "\n  ".join(stale)
