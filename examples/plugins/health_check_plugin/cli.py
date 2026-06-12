import asyncio
import json
import logging
from typing import Any

import click
from sqlalchemy import text


@click.command("health")
def health_command() -> None:
    """Displays application database health and metrics in JSON format.

    Plugin-contributed commands are registered on the CLI group as-is, so
    they drive their own event loop: the command body is an ``async def``
    executed with one ``asyncio.run`` per invocation, mirroring the
    ``_with_app_context`` pattern in ``api/manage.py``.
    """
    from api.config import settings
    from api.database import build_async_engine
    from api.extensions import db

    logger = logging.getLogger(__name__)

    async def _run() -> dict[str, Any]:
        created_engine = False
        if db._engine is None:
            db.init_app(engine=build_async_engine())
            created_engine = True

        try:
            await db.session.execute(text("SELECT 1"))
            db_status = "connected"
            error = None
            logger.info("Database connection successful.")

            tables_query = text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public';
            """)
            tables = (await db.session.execute(tables_query)).fetchall()

            table_sizes = {}
            for table in tables:
                table_name = table[0]
                row_count_query = text(f"SELECT COUNT(*) FROM {table_name}")
                row_count = (await db.session.execute(row_count_query)).scalar()
                table_sizes[table_name] = row_count

        except Exception as e:
            db_status = "disconnected"
            error = str(e)
            table_sizes = {}
            logger.error(f"Database connection error: {error}")
        finally:
            await db.remove()
            if created_engine:
                # Connections must close on the loop that created them.
                await db.engine.dispose()

        return {
            "status": "ok" if db_status == "connected" else "error",
            "database": db_status,
            "tables": table_sizes,
            "version": getattr(settings, "APP_VERSION", "Not Defined"),
            **({"error": error} if error else {}),
        }

    status = asyncio.run(_run())

    logger.info(f"Health status: {status}")
    click.echo(json.dumps(status))
