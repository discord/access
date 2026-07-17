"""Click-based CLI for Access management commands.

Each command runs inside a per-invocation database scope set up by
`_with_app_context`.

Run via:
    access init <admin_email>
    access sync
    access notify
    python -m api.cli <command>
"""

from __future__ import annotations

import asyncio
import functools
import uuid
from typing import Any, Callable, List, TypeVar

import click
from sqlalchemy import func, or_, select

F = TypeVar("F", bound=Callable[..., Any])


def _with_app_context(func: F) -> F:
    """Establish a per-invocation app context for a Click command: bind the
    SQLAlchemy engine, eagerly load every plugin type, and set up a
    request-scoped session keyed to the CLI run. Mirrors the spirit of the
    pre-migration `with app.app_context()` block.

    This decorator is the sync/async boundary for the CLI: Click commands
    stay synchronous entry points, while the decorated command body is an
    `async def` driven by a single `asyncio.run` per invocation."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from api.app import _configure_logging, _configure_okta, _configure_sentry
        from api.database import build_async_engine
        from api.extensions import _session_scope, db
        from api.plugins import load_plugins

        # Mirror create_app()'s bootstrap so CLI runs get the same
        # token-redacting log filter, Sentry wiring, and OktaService
        # initialization. Without _configure_okta() in particular, the
        # module-level `okta` singleton is missing `okta_client` and any
        # command that touches Okta (sync, notify) raises AttributeError.
        _configure_logging()
        _configure_sentry()
        _configure_okta()

        async def _run() -> Any:
            # The async engine (and any asyncpg/aiosqlite connections it
            # creates) must be created and disposed on the event loop that
            # uses it, so engine binding happens inside asyncio.run.
            created_engine = False
            if db._engine is None:
                db.init_app(engine=build_async_engine())
                created_engine = True
            # Trigger plugin discovery once per CLI run. Notification,
            # conditional access, and app-group-lifecycle hooks are all
            # consumed by the `sync` / `notify` / `sync-app-group-memberships`
            # commands; the `init` family doesn't need them but the call is
            # cheap (memoized).
            load_plugins()
            token = _session_scope.set(f"cli-{uuid.uuid4().hex}")
            try:
                return await func(*args, **kwargs)
            finally:
                try:
                    await db.session.commit()
                except Exception:
                    await db.session.rollback()
                await db.remove()
                _session_scope.reset(token)
                # Connections opened by the async drivers must be closed on
                # the loop that created them. Only dispose when this
                # invocation created the engine — if it was already bound
                # (tests), the owner is responsible for disposal.
                if created_engine:
                    await db.engine.dispose()

        return asyncio.run(_run())

    return wrapper  # type: ignore[return-value]


@click.group()
def cli() -> None:
    """Access CLI."""


def _load_plugin_commands() -> None:
    """Register Click commands published via the `access.commands` entry point.

    Plugins declare commands in their `setup.py` like:
        entry_points={"access.commands": ["health=my_plugin.cli:health_command"]}
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return
    try:
        eps = entry_points(group="access.commands")
    except TypeError:
        # Older importlib.metadata returns a dict
        eps = entry_points().get("access.commands", [])
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
    "--group-batch-size",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="Maximum number of groups whose Okta memberships/ownerships are fetched from Okta concurrently.",
)
@_with_app_context
async def sync(
    sync_groups_authoritatively: bool,
    sync_group_memberships_authoritatively: bool,
    group_batch_size: int,
) -> None:
    """Sync users/groups/memberships from Okta to Access and expire stale requests."""
    from sentry_sdk import start_transaction

    from api.config import settings
    from api.services import okta
    from api.syncer import (
        expire_access_requests,
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
                batch_size=group_batch_size,
            )
            if settings.OKTA_USE_GROUP_OWNERS_API:
                await sync_group_ownerships(
                    act_as_authority=sync_group_memberships_authoritatively,
                    groups=groups,
                    group_ids_with_group_rules=group_ids_with_group_rules,
                    batch_size=group_batch_size,
                )
            await expire_access_requests()
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


@cli.command("sync-app-group-memberships")
@_with_app_context
async def sync_app_group_memberships() -> None:
    """Invoke the periodic membership sync hook for all apps with app group lifecycle plugins configured."""
    from api.extensions import db
    from api.models import App
    from api.plugins._async_dispatch import run_hooks_to_completion
    from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook

    click.echo("Starting app group lifecycle plugin sync")

    apps: List[App] = list(
        (
            await db.session.scalars(
                select(App).where(App.deleted_at.is_(None)).where(App.app_group_lifecycle_plugin.isnot(None))
            )
        ).all()
    )

    if len(apps) == 0:
        click.echo("No apps with app group lifecycle plugins configured")
        return

    click.echo(f"Found {len(apps)} app(s) with plugins configured")

    hook = get_app_group_lifecycle_hook()

    for app in apps:
        click.echo(f"Syncing app '{app.name}' (plugin: {app.app_group_lifecycle_plugin})")
        # App-group-lifecycle hooks are native async: awaited directly
        # with the AsyncSession, no run_sync bridge. run_hooks_to_completion uses
        # asyncio.wait (not gather) and logs any plugin failure itself.
        _, exceptions = await run_hooks_to_completion(
            hook.sync_all_group_membership(session=db.session, app=app, plugin_id=app.app_group_lifecycle_plugin),
            context=f"sync_all_group_membership for app '{app.name}'",
        )
        if exceptions:
            await db.session.rollback()
            click.echo(f"  ✗ Failed to sync app '{app.name}': {exceptions[0]}", err=True)
            continue
        try:
            await db.session.commit()
            click.echo(f"  ✓ Synced app '{app.name}'")
        except Exception as e:
            await db.session.rollback()
            click.echo(f"  ✗ Failed to sync app '{app.name}': {e}", err=True)

    click.echo("Completed app group lifecycle plugin sync")


if __name__ == "__main__":
    cli()
