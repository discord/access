import logging

import click
from flask.cli import with_appcontext
from sqlalchemy import text


@click.command("health")
@with_appcontext
def health_command() -> None:
    """Displays application database health and metrics in JSON format."""
    from flask import current_app, json

    from api.extensions import db

    logger = logging.getLogger(__name__)

    try:
        # Perform a simple database health check using SQLAlchemy
        db.session.execute(text("SELECT 1"))
        db_status = "connected"
        error = None
        logger.info("Database connection successful.")

        # Retrieve all table names and their row counts
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

    # Prepare the health status response
    status = {
        "status": "ok" if db_status == "connected" else "error",
        "database": db_status,
        "tables": table_sizes,
        "version": current_app.config.get("APP_VERSION", "Not Defined"),
        **({"error": error} if error else {}),
    }

    # Log the health status
    logger.info(f"Health status: {status}")

    # Output the health status as a JSON string
    click.echo(json.dumps(status))
