from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import UserAuditResource, UserList, UserResource

bp_name = "api-users"
bp_url_prefix = "/api/users"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(UserResource, "/<string:user_id>", endpoint="user_by_id")
api.add_resource(UserAuditResource, "/<string:user_id>/audit", endpoint="user_audit_by_id")
api.add_resource(UserList, "", endpoint="users")


def register_docs() -> None:
    docs.register(UserResource, blueprint=bp_name, endpoint="user_by_id")
    docs.register(UserList, blueprint=bp_name, endpoint="users")
