from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import AppList, AppResource

bp_name = "api-apps"
bp_url_prefix = "/api/apps"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(AppResource, "/<string:app_id>", endpoint="app_by_id")
api.add_resource(AppList, "", endpoint="apps")


def register_docs() -> None:
    docs.register(AppResource, blueprint=bp_name, endpoint="app_by_id")
    docs.register(AppList, blueprint=bp_name, endpoint="apps")

