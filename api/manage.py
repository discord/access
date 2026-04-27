"""Click-based CLI for Access management commands.

Replaces the Flask-CLI commands (`flask init`, `flask sync`, `flask notify`,
etc.) that the project used pre-FastAPI migration. Each command runs inside
a per-invocation database scope set up by `_with_db_context`.

Run via:
    access init <admin_email>
    access sync
    access notify
    python -m api.manage <command>
"""

from __future__ import annotations

import functools
import uuid
from typing import Any, Callable, List, TypeVar

import click

F = TypeVar("F", bound=Callable[..., Any])


def _with_db_context(func: F) -> F:
    """Initialize the SQLAlchemy engine + scope before running the command."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from api.database import build_engine
        from api.extensions import _session_scope, db

        if db._engine is None:
            db.init_app(engine=build_engine())
        token = _session_scope.set(f"cli-{uuid.uuid4().hex}")
        try:
            return func(*args, **kwargs)
        finally:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            db.remove()
            _session_scope.reset(token)

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
@_with_db_context
def init(admin_okta_user_email: str) -> None:
    """Import users/groups/memberships from Okta and create the built-in Access app."""
    _import_from_okta()
    _init_builtin_apps(admin_okta_user_email)


@cli.command("import-from-okta")
@_with_db_context
def import_from_okta() -> None:
    """Import users/groups/memberships from Okta."""
    _import_from_okta()


def _import_from_okta() -> None:
    from api.extensions import db
    from api.models import OktaGroup, OktaUser, OktaUserGroupMember
    from api.services import okta

    click.echo("Starting Okta Import")
    if db.session.query(db.func.count(OktaUser.id)).scalar() > 0:
        click.echo("Skipping import of Okta Users as they were previously imported")
    else:
        click.echo("Importing Okta Users")
        user_type_to_user_attrs_to_titles: dict[str, Any] = {}

        users = okta.list_users()
        for user in users:
            if user.type.id not in user_type_to_user_attrs_to_titles:
                user_type_to_user_attrs_to_titles[user.type.id] = okta.get_user_schema(
                    user.type.id
                ).user_attrs_to_titles()

            user_attrs_to_titles = user_type_to_user_attrs_to_titles[user.type.id]

            db.session.add(user.update_okta_user(OktaUser(), user_attrs_to_titles))

        db.session.commit()

    if db.session.query(db.func.count(OktaGroup.id)).scalar() > 0:
        click.echo("Skipping import of Okta Groups as they were previously imported")
    else:
        click.echo("Importing Okta Groups")

        # Consider groups with group rules assigning to them as unmanaged by Access
        group_ids_with_group_rules = okta.list_groups_with_active_rules()
        groups = okta.list_groups()
        for group in groups:
            db.session.add(group.update_okta_group(OktaGroup(), group_ids_with_group_rules))
        db.session.commit()

    if db.session.query(db.func.count(OktaUserGroupMember.id)).scalar() > 0:
        click.echo("Skipping import of Okta Group Memberships as they were previously imported")
    else:
        click.echo("Importing Okta Group Memberships")
        groups = okta.list_groups()
        for group in groups:
            members = okta.list_users_for_group(group.id)
            for member in members:
                if member.get_deleted_at() is None:
                    db.session.add(OktaUserGroupMember(user_id=member.id, group_id=group.id))
        db.session.commit()
    click.echo("Completed Okta Import")


@cli.command("init-builtin-apps")
@click.argument("admin_okta_user_email")
@_with_db_context
def init_builtin_apps(admin_okta_user_email: str) -> None:
    """Create the built-in Access app and owner group."""
    _init_builtin_apps(admin_okta_user_email)


def _init_builtin_apps(admin_okta_user_email: str) -> None:
    from api.extensions import db
    from api.models import App, OktaUser
    from api.operations import CreateApp

    existing_app = App.query.filter(App.name == App.ACCESS_APP_RESERVED_NAME).filter(App.deleted_at.is_(None)).first()
    if existing_app is not None:
        click.echo("Access app and groups already exist, skipping init of built-in apps")
        return

    admin_okta_user = (
        OktaUser.query.filter(OktaUser.deleted_at.is_(None))
        .filter(
            db.or_(
                OktaUser.id == admin_okta_user_email,
                OktaUser.email.ilike(admin_okta_user_email),
            )
        )
        .first()
    )

    if admin_okta_user is None:
        click.echo(f"Admin Okta user not found with email or id {admin_okta_user_email}")
        return

    click.echo("Creating Access app and groups")
    CreateApp(
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
@_with_db_context
def sync(sync_groups_authoritatively: bool, sync_group_memberships_authoritatively: bool) -> None:
    """Sync users/groups/memberships from Okta to Access and expire stale requests."""
    from sentry_sdk import start_transaction

    from api.config import settings
    from api.syncer import (
        expire_access_requests,
        sync_group_memberships,
        sync_group_ownerships,
        sync_groups,
        sync_users,
    )

    with start_transaction(op="sync"):
        sync_users()
        sync_groups(act_as_authority=sync_groups_authoritatively)
        sync_group_memberships(act_as_authority=sync_group_memberships_authoritatively)
        if settings.OKTA_USE_GROUP_OWNERS_API:
            sync_group_ownerships(act_as_authority=sync_group_memberships_authoritatively)
        expire_access_requests()


@cli.command("fix-unmanaged-groups")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@_with_db_context
def fix_unmanaged_groups(dry_run: bool) -> None:
    """Verify and fix unmanaged-group state in Access against Okta."""
    from api.integrity import verify_and_fix_unmanaged_groups

    verify_and_fix_unmanaged_groups(dry_run=dry_run)


@cli.command("fix-role-memberships")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@_with_db_context
def fix_role_memberships(dry_run: bool) -> None:
    """Verify and fix role-membership state in Access."""
    from api.integrity import verify_and_fix_role_memberships

    verify_and_fix_role_memberships(dry_run=dry_run)


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
@_with_db_context
def notify(owner: bool, role_owner: bool) -> None:
    """Send expiring-access notifications."""
    from api.syncer import (
        expiring_access_notifications_owner,
        expiring_access_notifications_role_owner,
        expiring_access_notifications_user,
    )

    if owner:
        expiring_access_notifications_owner()
    elif role_owner:
        expiring_access_notifications_role_owner()
    else:
        expiring_access_notifications_user()


@cli.command("sync-app-group-memberships")
@_with_db_context
def sync_app_group_memberships() -> None:
    """Invoke the periodic membership sync hook for all apps with app group lifecycle plugins configured."""
    from api.extensions import db
    from api.models import App
    from api.plugins.app_group_lifecycle import get_app_group_lifecycle_hook

    click.echo("Starting app group lifecycle plugin sync")

    apps: List[App] = (
        App.query.filter(App.deleted_at.is_(None)).filter(App.app_group_lifecycle_plugin.isnot(None)).all()
    )

    if len(apps) == 0:
        click.echo("No apps with app group lifecycle plugins configured")
        return

    click.echo(f"Found {len(apps)} app(s) with plugins configured")

    hook = get_app_group_lifecycle_hook()

    for app in apps:
        click.echo(f"Syncing app '{app.name}' (plugin: {app.app_group_lifecycle_plugin})")
        try:
            hook.sync_all_group_membership(session=db.session, app=app, plugin_id=app.app_group_lifecycle_plugin)
            db.session.commit()
            click.echo(f"  ✓ Synced app '{app.name}'")
        except Exception as e:
            db.session.rollback()
            click.echo(f"  ✗ Failed to sync app '{app.name}': {e}", err=True)

    click.echo("Completed app group lifecycle plugin sync")


if __name__ == "__main__":
    cli()
