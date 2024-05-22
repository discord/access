from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import AccessRequestList, AccessRequestResource

bp_name = "api-access-requests"
bp_url_prefix = "/api/requests"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(
    AccessRequestResource,
    "/<string:access_request_id>",
    endpoint="access_request_by_id",
)
api.add_resource(AccessRequestList, "", endpoint="access_requests")


def register_docs() -> None:
    docs.register(AccessRequestResource, blueprint=bp_name, endpoint="access_request_by_id")
    docs.register(AccessRequestList, blueprint=bp_name, endpoint="access_requests")
