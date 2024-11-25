from flask import Flask


def init_app(app: Flask) -> None:
    from .cli import health_command

    app.cli.add_command(health_command)
