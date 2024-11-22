import click
from flask import current_app, json
from flask.cli import with_appcontext
from sqlalchemy import text


@click.command("health")
@with_appcontext
def health_command() -> None:
    """Displays application health and metrics in JSON format."""
    try:
        # Get the 'db' extension from the current app context
        db = current_app.extensions["sqlalchemy"].db
        # Perform database health check
        db.session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = "disconnected"
        error = str(e)

    status = {
        "status": "ok" if db_status == "connected" else "error",
        "database": db_status,
        "version": current_app.config.get("APP_VERSION", "Not Defined"),
    }
    if db_status == "disconnected":
        status["error"] = error

    click.echo(json.dumps(status))
