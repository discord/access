from typing import Any

from flask import Blueprint, jsonify, request
from werkzeug import exceptions

bp_name = "exceptions"
bp = Blueprint(bp_name, __name__, static_folder="../../build")


@bp.app_errorhandler(exceptions.InternalServerError)
def _handle_internal_server_error(ex: exceptions.InternalServerError) -> Any:
    if request.path.startswith("/api/"):
        return jsonify(message=str(ex)), ex.code
    else:
        return ex


@bp.app_errorhandler(exceptions.NotFound)
def _handle_not_found_error(ex: exceptions.NotFound) -> Any:
    if request.path.startswith("/api/"):
        return {"message": "Not Found"}, ex.code
    else:
        # So that the React SPA functions
        return bp.send_static_file("index.html")
