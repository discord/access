"""Click-based CLI for Access management commands.

`app_context` bootstraps the database engine, plugins, and session scope that
every command (and the `shell` REPL) runs inside.

Run via:
    access init <admin_email>
    access sync
    access notify
    access shell
    python -m api.cli <command>
"""

from __future__ import annotations

import asyncio
import ast
import code
import functools
import types
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Callable, TypeVar, cast

import click
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

F = TypeVar("F", bound=Callable[..., Any])


@asynccontextmanager
async def app_context(*, scope: str | None = None) -> AsyncIterator[None]:
    """Bootstrap the application context an out-of-request entrypoint needs.

    Binds the SQLAlchemy async engine, mirrors `create_app()`'s logging /
    Sentry / Okta configuration, and eagerly loads every plugin type, so that
    `db.session`, the operation classes, and the plugin hooks all work the way
    they do under the API server. On exit it commits the session, closes it,
    and disposes the engine if it created one.

    Must be entered from inside a running event loop: the async engine, and
    the asyncpg/aiosqlite connections it opens, have to be created and
    disposed on the loop that uses them.

    Args:
        scope: Session scope to bind for the duration, isolating this
            entrypoint's `db.session` from any other. Omit to leave the
            ambient scope in place, which for an ad-hoc caller is the
            process-global default (see `_session_scope`).

    Yields:
        None. Callers reach the session through `api.extensions.db`.
    """
    from api.app import _configure_logging, _configure_okta, _configure_sentry
    from api.database import build_async_engine
    from api.extensions import _session_scope, db
    from api.plugins import load_plugins

    # Without _configure_okta() in particular, the module-level `okta`
    # singleton is missing `okta_client` and anything that touches Okta
    # raises AttributeError.
    _configure_logging()
    _configure_sentry()
    _configure_okta()

    created_engine = False
    if db._engine is None:
        db.init_app(engine=build_async_engine())
        created_engine = True
    # Every `get_*_hook()` is memoized, so this is cheap for the entrypoints
    # (the `init` family) that never fire a hook.
    load_plugins()
    token = _session_scope.set(scope) if scope is not None else None
    try:
        yield
    finally:
        try:
            await db.session.commit()
        except Exception:
            await db.session.rollback()
        await db.remove()
        if token is not None:
            _session_scope.reset(token)
        # Connections opened by the async drivers must be closed on the loop
        # that created them. Only dispose an engine this context created — a
        # borrowed one (tests) belongs to its owner.
        if created_engine:
            await db.engine.dispose()


def _with_app_context(func: F) -> F:
    """Run a Click command body inside its own `app_context`.

    This decorator is the sync/async boundary for the CLI: Click commands
    stay synchronous entry points, while the decorated command body is an
    `async def` driven by a single `asyncio.run` per invocation. Each run
    gets its own session scope so concurrent invocations never share a
    session."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        async def _run() -> Any:
            async with app_context(scope=f"cli-{uuid.uuid4().hex}"):
                return await func(*args, **kwargs)

        return asyncio.run(_run())

    return cast(F, wrapper)


@click.group()
def cli() -> None:
    """Access CLI."""


def _load_plugin_commands() -> None:
    """Register Click commands published via the `access.commands` entry point.

    Plugins declare commands in their pyproject.toml like:
        [project.entry-points."access.commands"]
        health = "my_plugin.cli:health_command"

    The group name must stay quoted: unquoted, TOML reads it as a `commands`
    table nested inside `access` and the command never reaches this loader.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return
    try:
        eps = entry_points(group="access.commands")
    except TypeError:
        # Older importlib.metadata returns a dict keyed by group; `EntryPoints`
        # (3.12+) has no `.get`, hence the ignore on this back-compat branch.
        eps = entry_points().get("access.commands", [])  # ty: ignore[unresolved-attribute]
    for ep in eps:
        try:
            command = ep.load()
        except Exception:
            continue
        if isinstance(command, click.Command):
            cli.add_command(command, name=ep.name)


_load_plugin_commands()


@cli.command("init")
@click.argument("admin_okta_user_email")
@_with_app_context
async def init(admin_okta_user_email: str) -> None:
    """Import users/groups/memberships from Okta and create the built-in Access app."""
    await _import_from_okta()
    await _init_builtin_apps(admin_okta_user_email)


@cli.command("import-from-okta")
@_with_app_context
async def import_from_okta() -> None:
    """Import users/groups/memberships from Okta."""
    await _import_from_okta()


async def _import_from_okta() -> None:
    from api.extensions import db
    from api.models import OktaGroup, OktaUser, OktaUserGroupMember
    from api.services import okta

    click.echo("Starting Okta Import")
    if (await db.session.scalar(select(func.count(OktaUser.id))) or 0) > 0:
        click.echo("Skipping import of Okta Users as they were previously imported")
    else:
        click.echo("Importing Okta Users")
        user_type_to_user_attrs_to_titles: dict[str, Any] = {}

        users = await okta.list_users()
        for user in users:
            if user.type.id not in user_type_to_user_attrs_to_titles:
                user_type_to_user_attrs_to_titles[user.type.id] = (
                    await okta.get_user_schema(user.type.id)
                ).user_attrs_to_titles()

            user_attrs_to_titles = user_type_to_user_attrs_to_titles[user.type.id]

            db.session.add(user.update_okta_user(OktaUser(), user_attrs_to_titles))

        await db.session.commit()

    if (await db.session.scalar(select(func.count(OktaGroup.id))) or 0) > 0:
        click.echo("Skipping import of Okta Groups as they were previously imported")
    else:
        click.echo("Importing Okta Groups")

        # Consider groups with group rules assigning to them as unmanaged by Access
        group_ids_with_group_rules = await okta.list_groups_with_active_rules()
        groups = await okta.list_groups()
        for group in groups:
            db.session.add(group.update_okta_group(OktaGroup(), group_ids_with_group_rules))
        await db.session.commit()

    if (await db.session.scalar(select(func.count(OktaUserGroupMember.id))) or 0) > 0:
        click.echo("Skipping import of Okta Group Memberships as they were previously imported")
    else:
        click.echo("Importing Okta Group Memberships")
        groups = await okta.list_groups()
        for group in groups:
            members = await okta.list_users_for_group(group.id)
            for member in members:
                if member.get_deleted_at() is None:
                    db.session.add(OktaUserGroupMember(user_id=member.id, group_id=group.id))
        await db.session.commit()
    click.echo("Completed Okta Import")


@cli.command("init-builtin-apps")
@click.argument("admin_okta_user_email")
@_with_app_context
async def init_builtin_apps(admin_okta_user_email: str) -> None:
    """Create the built-in Access app and owner group."""
    await _init_builtin_apps(admin_okta_user_email)


async def _init_builtin_apps(admin_okta_user_email: str) -> None:
    from api.extensions import db
    from api.models import App, OktaUser
    from api.operations import CreateApp

    existing_app = (
        await db.session.scalars(
            select(App).where(App.name == App.ACCESS_APP_RESERVED_NAME).where(App.deleted_at.is_(None))
        )
    ).first()
    if existing_app is not None:
        click.echo("Access app and groups already exist, skipping init of built-in apps")
        return

    admin_okta_user = (
        await db.session.scalars(
            select(OktaUser)
            .where(OktaUser.deleted_at.is_(None))
            .where(
                or_(
                    OktaUser.id == admin_okta_user_email,
                    OktaUser.email.ilike(admin_okta_user_email),
                )
            )
        )
    ).first()

    if admin_okta_user is None:
        click.echo(f"Admin Okta user not found with email or id {admin_okta_user_email}")
        return

    click.echo("Creating Access app and groups")
    await CreateApp(
        owner_id=admin_okta_user.id,
        app={"name": App.ACCESS_APP_RESERVED_NAME, "description": f"The {App.ACCESS_APP_RESERVED_NAME} Portal"},
    ).execute()


@cli.command("sync")
@click.option(
    "--sync-groups-authoritatively",
    is_flag=True,
    show_default=True,
    default=False,
    help="Sync groups from Access to Okta",
)
@click.option(
    "--sync-group-memberships-authoritatively",
    is_flag=True,
    show_default=True,
    default=False,
    help="Sync group memberships from Access to Okta",
)
@click.option(
    "--group-fetch-concurrency",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="Maximum number of groups whose Okta memberships/ownerships are fetched from Okta concurrently.",
)
@_with_app_context
async def sync(
    sync_groups_authoritatively: bool,
    sync_group_memberships_authoritatively: bool,
    group_fetch_concurrency: int,
) -> None:
    """Sync users/groups/memberships from Okta to Access and expire stale requests."""
    from sentry_sdk import start_transaction

    from api.config import settings
    from api.services import okta
    from api.syncer import (
        expire_access_requests,
        expire_group_requests,
        expire_role_requests,
        sync_group_memberships,
        sync_group_ownerships,
        sync_groups,
        sync_users,
    )

    # Pool one Okta client (and its aiohttp connector) for the whole run so the
    # concurrent per-group membership/ownership fan-out reuses connections.
    # No-op when Okta isn't configured (dev/test).
    await okta.start_pooled_client()
    try:
        with start_transaction(op="sync"):
            await sync_users()

            # Fetch the active group rules once and reuse them across every pass
            # — group rules don't change over the course of a sync run.
            group_ids_with_group_rules = await okta.list_groups_with_active_rules()

            await sync_groups(
                act_as_authority=sync_groups_authoritatively,
                group_ids_with_group_rules=group_ids_with_group_rules,
            )

            # Re-list groups once after sync_groups (which can create or delete
            # groups in authoritative mode) and reuse the snapshot for both the
            # membership and ownership passes — neither mutates the group set.
            groups = await okta.list_groups()

            await sync_group_memberships(
                act_as_authority=sync_group_memberships_authoritatively,
                groups=groups,
                group_ids_with_group_rules=group_ids_with_group_rules,
                concurrency=group_fetch_concurrency,
            )
            if settings.OKTA_USE_GROUP_OWNERS_API:
                await sync_group_ownerships(
                    act_as_authority=sync_group_memberships_authoritatively,
                    groups=groups,
                    group_ids_with_group_rules=group_ids_with_group_rules,
                    concurrency=group_fetch_concurrency,
                )
            await expire_access_requests()
            await expire_role_requests()
            await expire_group_requests()
    finally:
        await okta.stop_pooled_client()


@cli.command("fix-unmanaged-groups")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@_with_app_context
async def fix_unmanaged_groups(dry_run: bool) -> None:
    """Verify and fix unmanaged-group state in Access against Okta."""
    from api.integrity import verify_and_fix_unmanaged_groups

    await verify_and_fix_unmanaged_groups(dry_run=dry_run)


@cli.command("fix-role-memberships")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@_with_app_context
async def fix_role_memberships(dry_run: bool) -> None:
    """Verify and fix role-membership state in Access."""
    from api.integrity import verify_and_fix_role_memberships

    await verify_and_fix_role_memberships(dry_run=dry_run)


@cli.command("cap-role-memberships")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@_with_app_context
async def cap_role_memberships_command(dry_run: bool) -> None:
    """Cap existing role memberships against time limits propagated from tagged groups."""
    from api.integrity import cap_role_memberships

    capped = await cap_role_memberships(dry_run=dry_run)
    click.echo(f"{'Would cap' if dry_run else 'Capped'} {capped} role membership(s)")


@cli.command("notify")
@click.option(
    "--owner",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will notify group owners instead of individuals",
)
@click.option(
    "--role-owner",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will notify role owners instead of individuals",
)
@_with_app_context
async def notify(owner: bool, role_owner: bool) -> None:
    """Send expiring-access notifications."""
    from api.syncer import (
        expiring_access_notifications_owner,
        expiring_access_notifications_role_owner,
        expiring_access_notifications_user,
    )

    if owner:
        await expiring_access_notifications_owner()
    elif role_owner:
        await expiring_access_notifications_role_owner()
    else:
        await expiring_access_notifications_user()


async def _sync_all_app_groups() -> int:
    """Invoke the `sync_group` hook once per active app group of every app with a lifecycle
    plugin configured.

    Each group is loaded, handed to its plugin, and committed as its own unit of work, so one
    group's failure can't strand the groups after it. That isolation lives here rather than in each
    plugin, which would otherwise have to reproduce it. Keep the boundary per-group: batching an
    app's groups into one transaction would also let advisory locks accumulate across the batch,
    which two overlapping runs iterating in different orders can deadlock on.

    Returns:
        The number of groups that failed to sync, which the CLI command turns into its exit status.
    """
    from api.extensions import db
    from api.models import App, AppGroup
    from api.plugins.app_group_lifecycle import AppGroupLifecycleHook, invoke_app_group_lifecycle_hook

    click.echo("Starting app group lifecycle plugin sync")

    # Scan for plain column values rather than ORM instances. A failed group below can roll the
    # whole session back, expiring *every* instance in its identity map -- `dirty_only` is False for
    # a top-level rollback, and `expire_on_commit=False` does not apply -- and re-reading an expired
    # attribute on an AsyncSession raises MissingGreenlet instead of quietly refreshing. Holding
    # only strings across that boundary keeps the loop's own bookkeeping (and its error messages)
    # independent of session state.
    # Ordered by app then group name so a run's sequence, and hence its output, is reproducible.
    group_rows = (
        await db.session.execute(
            select(AppGroup.id, AppGroup.name, App.name, App.app_group_lifecycle_plugin)
            .join(App, AppGroup.app_id == App.id)
            .where(App.deleted_at.is_(None))
            .where(App.app_group_lifecycle_plugin.isnot(None))
            .where(AppGroup.deleted_at.is_(None))
            .order_by(App.name, AppGroup.name)
        )
    ).all()

    if len(group_rows) == 0:
        click.echo("No app groups with app group lifecycle plugins configured")
        return 0

    click.echo(f"Found {len(group_rows)} app group(s) with plugins configured")

    skipped = 0
    failures = 0
    for group_id, group_name, app_name, plugin_id in group_rows:
        click.echo(f"Syncing group '{group_name}' (app: '{app_name}', plugin: {plugin_id})")

        # Eager-load `group.app`, which the `sync_group` hookspec promises and which
        # invoke_app_group_lifecycle_hook itself reads to resolve the plugin id. The relationship
        # is lazy="raise_on_sql", so reading it off an unloaded group raises instead of querying.
        # Loaded per iteration rather than once up front because a preceding group's rollback can
        # expire the whole identity map; `populate_existing` because the durable-status re-apply
        # commits its own transaction, and instances loaded before that commit are not expired by
        # it (expire_on_commit=False), so a re-visited row could otherwise be served stale.
        group = (
            await db.session.scalars(
                select(AppGroup)
                .where(AppGroup.id == group_id)
                .where(AppGroup.deleted_at.is_(None))
                .options(joinedload(AppGroup.app))
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if group is None:
            # Deleted between the scan above and now; there is nothing left to sync.
            skipped += 1
            click.echo(f"  - Skipped group '{group_name}': no longer present")
            continue

        # invoke_app_group_lifecycle_hook owns the whole unit of work: it builds the plugin's
        # context, drives the hook via run_hooks_to_completion (asyncio.wait), then commits, or
        # rolls back and re-applies the plugin's durable status. It returns plugin exceptions
        # rather than raising, and re-derives the plugin id from `group.app` -- authoritative if an
        # app is reconfigured mid-run.
        exceptions = await invoke_app_group_lifecycle_hook(
            AppGroupLifecycleHook.SYNC_GROUP, session=db.session, group=group
        )
        if exceptions:
            failures += 1
            click.echo(f"  ✗ Failed to sync group '{group_name}' (app: '{app_name}'): {exceptions[0]}", err=True)
        else:
            click.echo(f"  ✓ Synced group '{group_name}'")

    click.echo(
        f"Completed app group lifecycle plugin sync with {failures} failures and {skipped} skipped, "
        f"out of {len(group_rows)} total"
    )
    return failures


@cli.command("sync-app-groups")
@_with_app_context
async def sync_app_groups() -> None:
    """Invoke the periodic group-sync hook for every group of every app with an app group lifecycle
    plugin configured."""
    failures = await _sync_all_app_groups()
    if failures:
        # Every group is attempted regardless, but the command must still exit non-zero:
        # this runs as a periodic job, so a run that left groups unreconciled has to be
        # visible as a failed run rather than only as stderr output.
        raise SystemExit(1)


def _shell_namespace() -> dict[str, Any]:
    """Build the names an `access shell` session starts with: the session
    facade, every ORM model and operation class, and the query and eager-load
    helpers those need.

    Returns:
        A fresh namespace dict, used as the console's globals so that names
        bound at the prompt persist across statements.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import and_, delete, update
    from sqlalchemy.orm import selectinload, selectin_polymorphic, with_polymorphic

    from api import models, operations
    from api.config import settings
    from api.extensions import db
    from api.services import okta

    namespace: dict[str, Any] = {
        "asyncio": asyncio,
        "db": db,
        "settings": settings,
        "okta": okta,
        "models": models,
        "operations": operations,
        # Query building. The loader options are here rather than left to an
        # import because `lazy="raise_on_sql"` makes reading an un-eager-loaded
        # relationship raise, so a session spent exploring needs them at hand.
        "select": select,
        "update": update,
        "delete": delete,
        "func": func,
        "or_": or_,
        "and_": and_,
        "joinedload": joinedload,
        "selectinload": selectinload,
        "selectin_polymorphic": selectin_polymorphic,
        "with_polymorphic": with_polymorphic,
        "UTC": UTC,
        "datetime": datetime,
        "timedelta": timedelta,
    }
    namespace.update({name: getattr(models, name) for name in models.__all__})
    namespace.update({name: getattr(operations, name) for name in operations.__all__})
    return namespace


class _AsyncConsole(code.InteractiveConsole):
    """An interactive console whose statements may use top-level `await`.

    Statements compile with `PyCF_ALLOW_TOP_LEVEL_AWAIT` and any resulting
    coroutine is driven by `runner`. Reusing one `asyncio.Runner` for the
    console's whole life is what makes the session coherent: a Runner keeps a
    single `contextvars.Context` across `run()` calls, so the session scope
    bound during bootstrap is the one each statement sees, and every statement
    shares the engine's event loop.
    """

    def __init__(self, namespace: dict[str, Any], runner: asyncio.Runner) -> None:
        super().__init__(locals=namespace)
        self.compile.compiler.flags |= ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        self._runner = runner

    def runcode(self, code: types.CodeType) -> None:
        # Called rather than exec'd so a code object compiled with the
        # top-level-await flag yields its coroutine instead of running to
        # completion. The code object is module-level, so its name
        # assignments still land in `self.locals` and persist across
        # statements.
        try:
            result = types.FunctionType(code, self.locals)()
            if asyncio.iscoroutine(result):
                self._runner.run(result)
        except SystemExit:
            raise
        except BaseException:
            self.showtraceback()


def _enable_readline(namespace: dict[str, Any]) -> None:
    """Turn on history, line editing, and tab completion against `namespace`.

    A no-op where `readline` is unavailable; the console still works without
    it."""
    try:
        import readline
        import rlcompleter
    except ImportError:
        return
    readline.set_completer(rlcompleter.Completer(namespace).complete)
    # macOS ships libedit under the readline name, which does not understand
    # GNU readline's binding syntax.
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


@cli.command("shell")
def shell() -> None:
    """Start an interactive REPL with the app context bootstrapped.

    Statements may use top-level `await`, which they need to: the session, the
    operation classes, and the Okta service are all async. Work is committed
    when the session ends.
    """
    from api.extensions import db

    namespace = _shell_namespace()
    _enable_readline(namespace)

    with asyncio.Runner() as runner:
        # `app_context` is entered and exited through an exit stack rather
        # than `async with`, because the console that runs in between is
        # synchronous and drives the loop one statement at a time.
        stack = AsyncExitStack()
        runner.run(stack.enter_async_context(app_context()))
        try:
            banner = (
                f"Access shell — {db.engine.url.render_as_string(hide_password=True)}\n"
                "Statements may use top-level `await`; writes commit on exit.\n"
                "In scope: db, models, operations, select/func/joinedload, okta, settings."
            )
            _AsyncConsole(namespace, runner).interact(banner=banner, exitmsg="")
        finally:
            runner.run(stack.aclose())


if __name__ == "__main__":
    cli()
