"""Alembic environment configured for plain SQLAlchemy (no Flask-Migrate)."""

from __future__ import with_statement

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import JSON

from api.config import settings
from api.database import build_async_engine
from api.extensions import Base

# Make sure all models are imported so Base.metadata is populated
import api.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

# Inject the SQLAlchemy URL from settings
db_url = settings.SQLALCHEMY_DATABASE_URI or "sqlite:///instance/access.db"
config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """Suppress spurious JSON type-change diffs during autogenerate/check.

    Postgres reflects JSON columns with an explicit ``astext_type`` that the
    models' ``JSON`` (and the ``JSONB`` variant, a subclass) don't carry, so
    `alembic check` reports a phantom type change. Treat any JSON-family pair
    as equal; fall back to Alembic's default comparison for everything else
    by returning ``None``.
    """
    if isinstance(inspected_type, JSON) and isinstance(metadata_type, JSON):
        return False
    return None


def _do_run_migrations(connection) -> None:
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("No changes in schema detected.")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        process_revision_directives=process_revision_directives,
        compare_type=compare_type,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through the async engine.

    Alembic's migration machinery is synchronous; `connection.run_sync`
    bridges it onto the AsyncEngine so the same engine builder (including
    the Cloud SQL IAM connector path) serves the app and migrations.
    """
    import asyncio

    async def _run() -> None:
        connectable = build_async_engine()
        async with connectable.connect() as connection:
            await connection.run_sync(_do_run_migrations)
        await connectable.dispose()

    asyncio.run(_run())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
