from typing import Any

from flask import Blueprint
from sqlalchemy import text

from api.extensions import db

bp_name = "api-health-check"
bp_url_prefix = "/api/healthz"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


@bp.route("", methods=["GET"])
def health_check() -> Any:
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500

    return {"status": "ok"}, 200
