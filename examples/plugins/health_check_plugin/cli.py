import json
import logging

import click
from sqlalchemy import text


@click.command("health")
def health_command() -> None:
    """Displays application database health and metrics in JSON format."""
    from api.config import settings
    from api.database import build_engine
    from api.extensions import db

    logger = logging.getLogger(__name__)

    if db._engine is None:
        db.init_app(engine=build_engine())

    try:
        db.session.execute(text("SELECT 1"))
        db_status = "connected"
        error = None
        logger.info("Database connection successful.")

        tables_query = text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public';
        """)
        tables = db.session.execute(tables_query).fetchall()

        table_sizes = {}
        for table in tables:
            table_name = table[0]
            row_count_query = text(f"SELECT COUNT(*) FROM {table_name}")
            row_count = db.session.execute(row_count_query).scalar()
            table_sizes[table_name] = row_count

    except Exception as e:
        db_status = "disconnected"
        error = str(e)
        table_sizes = {}
        logger.error(f"Database connection error: {error}")

    status = {
        "status": "ok" if db_status == "connected" else "error",
        "database": db_status,
        "tables": table_sizes,
        "version": getattr(settings, "APP_VERSION", "Not Defined"),
        **({"error": error} if error else {}),
    }

    logger.info(f"Health status: {status}")
    click.echo(json.dumps(status))
