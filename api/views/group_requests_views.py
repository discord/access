from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import GroupRequestList, GroupRequestResource

bp_name = "api-group-requests"
bp_url_prefix = "/api/group-requests"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(
    GroupRequestResource,
    "/<string:group_request_id>",
    endpoint="group_request_by_id",
)
api.add_resource(GroupRequestList, "", endpoint="group_requests")


def register_docs() -> None:
    docs.register(GroupRequestResource, blueprint=bp_name, endpoint="group_request_by_id")
    docs.register(GroupRequestList, blueprint=bp_name, endpoint="group_requests")
