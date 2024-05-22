from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import RoleAuditResource, RoleList, RoleMemberResource, RoleResource

bp_name = "api-roles"
bp_url_prefix = "/api/roles"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(RoleResource, "/<string:role_id>", endpoint="role_by_id")
api.add_resource(RoleAuditResource, "/<string:role_id>/audit", endpoint="role_audit_by_id")
api.add_resource(RoleMemberResource, "/<string:role_id>/members", endpoint="role_members_by_id")
api.add_resource(RoleList, "", endpoint="roles")


def register_docs() -> None:
    docs.register(RoleResource, blueprint=bp_name, endpoint="role_by_id")
    docs.register(RoleMemberResource, blueprint=bp_name, endpoint="role_members_by_id")
    docs.register(RoleList, blueprint=bp_name, endpoint="roles")
