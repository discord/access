from flask import Blueprint

from api.extensions import Api, docs
from api.views.resources import GroupRoleAuditResource, UserGroupAuditResource

bp_name = "api-audit"
bp_url_prefix = "/api/audit"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

api = Api(bp)

api.add_resource(UserGroupAuditResource, "/users", endpoint="users_and_groups")
api.add_resource(GroupRoleAuditResource, "/groups", endpoint="groups_and_roles")


def register_docs() -> None:
    docs.register(UserGroupAuditResource, blueprint=bp_name, endpoint="users_and_groups")
    docs.register(GroupRoleAuditResource, blueprint=bp_name, endpoint="groups_and_roles")
