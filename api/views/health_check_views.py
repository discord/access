from typing import Any

from flask import Blueprint

bp_name = "health_check"
bp_url_prefix = "/api/healthz"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


@bp.route("/", methods=["GET"])
def health_check() -> Any:
    return {"status": "ok"}, 200
