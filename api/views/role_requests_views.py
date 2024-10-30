from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import RoleRequestList, RoleRequestResource

bp_name = "api-role-requests"
bp_url_prefix = "/api/role_requests"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(
    RoleRequestResource,
    "/<string:role_request_id>",
    endpoint="role_request_by_id",
)
api.add_resource(RoleRequestList, "", endpoint="role_requests")


def register_docs() -> None:
    docs.register(RoleRequestResource, blueprint=bp_name, endpoint="role_request_by_id")
    docs.register(RoleRequestList, blueprint=bp_name, endpoint="role_requests")
