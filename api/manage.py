import click
from flask.cli import with_appcontext


@click.command("init")
@click.argument("admin_okta_user_email")
@with_appcontext
def init(admin_okta_user_email: str) -> None:
    _import_from_okta()
    _init_builtin_apps(admin_okta_user_email)


@click.command("import-from-okta")
@with_appcontext
def import_from_okta() -> None:
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
        user_type_to_user_attrs_to_titles = {}

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


@click.command("init-builtin-apps")
@click.argument("admin_okta_user_id")
@with_appcontext
def init_builtin_apps(admin_okta_user_email: str) -> None:
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


@click.command("sync")
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
@with_appcontext
def sync(sync_groups_authoritatively: bool, sync_group_memberships_authoritatively: bool) -> None:
    from sentry_sdk import start_transaction
    from flask import current_app

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
        if current_app.config["OKTA_USE_GROUP_OWNERS_API"]:
            sync_group_ownerships(act_as_authority=sync_group_memberships_authoritatively)
        expire_access_requests()


@click.command("fix-unmanaged-groups")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@with_appcontext
def fix_unmanaged_groups(dry_run: bool) -> None:
    from api.integrity import (
        verify_and_fix_unmanaged_groups,
    )

    verify_and_fix_unmanaged_groups(dry_run=dry_run)


@click.command("fix-role-memberships")
@click.option(
    "--dry-run",
    is_flag=True,
    show_default=True,
    default=False,
    help="If set will run as dry run and not make any changes",
)
@with_appcontext
def fix_role_memberships(dry_run: bool) -> None:
    from api.integrity import (
        verify_and_fix_role_memberships,
    )

    verify_and_fix_role_memberships(dry_run=dry_run)


@click.command("notify")
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
@with_appcontext
def notify(owner: bool, role_owner: bool) -> None:
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
